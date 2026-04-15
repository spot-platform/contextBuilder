"""Layer 2 — SpotDetail 전용 rule validation.

플랜 §5 Layer 2 표 + §3 SpotDetail 스키마 (description 3~6문장, cost_breakdown 총액 등).

**LLM 호출 금지.** 순수 Python / yaml / rapidfuzz(선택).

구현 rule:
    1. rule_description_sentence_count   — description 3~6 문장
    2. rule_category_consistency_detail  — detail 본문에 카테고리 deny 키워드 금지
    3. rule_cost_breakdown_total         — cost_breakdown 합계 ≈ expected_cost_per_person
    4. rule_host_intro_length            — supporter_required=True 이면 host_intro ≥ 60자
    5. rule_policy_notes_safe            — policy_notes 금지어

rule 함수 시그니처는 feed 와 동일: ``(payload, spec, rules) -> list[Rejection]``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from pipeline.spec.models import ContentSpec
from pipeline.validators.rules import load_feed_rules
from pipeline.validators.types import Rejection, ValidationResult

# ---------------------------------------------------------------------------
# 상수 / 로더
# ---------------------------------------------------------------------------

DEFAULT_RULES_DIR = Path("config/rules")

# 한국어·영어 공통 종결 부호 (schema.py 와 동일 정책).
_SENTENCE_END_RE = re.compile(r"[.!?。！？]+")


def _count_sentences(text: str) -> int:
    if not text or not text.strip():
        return 0
    matches = _SENTENCE_END_RE.findall(text)
    if not matches:
        return 1
    return len(matches)


def load_detail_rules(rules_dir: Optional[Path] = None) -> Dict[str, Any]:
    """``detail_rules.yaml`` + ``feed_rules.yaml`` (카테고리 deny 재사용) 병합."""
    base = Path(rules_dir) if rules_dir else DEFAULT_RULES_DIR
    detail_path = base / "detail_rules.yaml"

    detail_yaml: Dict[str, Any] = {}
    if detail_path.exists():
        with detail_path.open("r", encoding="utf-8") as fh:
            detail_yaml = yaml.safe_load(fh) or {}

    # category deny/allow 는 feed_rules.yaml 재사용.
    feed_like = load_feed_rules(base)

    return {
        "detail": detail_yaml,
        "categories": feed_like.get("categories", {}),
    }


# ---------------------------------------------------------------------------
# 본문 blob helper
# ---------------------------------------------------------------------------


def _detail_text_blob(payload: Dict[str, Any]) -> str:
    """detail 본문 통합 — 카테고리/금기어 검색 용."""
    parts: List[str] = []
    for key in (
        "title",
        "description",
        "activity_purpose",
        "progress_style",
        "target_audience",
        "host_intro",
        "policy_notes",
    ):
        v = payload.get(key)
        if isinstance(v, str):
            parts.append(v)
    materials = payload.get("materials")
    if isinstance(materials, list):
        parts.extend(str(m) for m in materials)
    cost_breakdown = payload.get("cost_breakdown")
    if isinstance(cost_breakdown, list):
        for row in cost_breakdown:
            if isinstance(row, dict):
                item = row.get("item")
                if isinstance(item, str):
                    parts.append(item)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Rule 1. description 문장 수
# ---------------------------------------------------------------------------


def rule_description_sentence_count(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """description 3~6 문장.

    schema 는 길이(80~800)만 제한하므로 "자연스러운 3~6문장" 은 rule 이 담당.
    """
    desc = payload.get("description")
    if not isinstance(desc, str):
        return []
    cfg = rules.get("detail") or {}
    lo = int(cfg.get("description_min_sentences", 3))
    hi = int(cfg.get("description_max_sentences", 6))

    n = _count_sentences(desc)
    if n < lo:
        return [
            Rejection(
                layer="rule",
                rejected_field="detail:description",
                reason="description_too_few_sentences",
                detail=f"description 문장 수 {n} < {lo}",
                instruction=(
                    f"description 을 {lo}~{hi} 문장으로 다시 작성하라. "
                    "소개→활동 설명→마무리 톤으로 문장을 나누면 자연스럽다."
                ),
            )
        ]
    if n > hi:
        return [
            Rejection(
                layer="rule",
                rejected_field="detail:description",
                reason="description_too_many_sentences",
                detail=f"description 문장 수 {n} > {hi}",
                instruction=(
                    f"description 을 {lo}~{hi} 문장 이내로 압축하라. "
                    "중복되는 수식어나 반복 정보를 정리하라."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Rule 2. 카테고리 deny 키워드 (detail 버전)
# ---------------------------------------------------------------------------


def rule_category_consistency_detail(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """detail 본문 전체에 spec.category deny 키워드 불포함."""
    cats = rules.get("categories") or {}
    cat_rules = cats.get(spec.category) or {}
    deny = cat_rules.get("deny_keywords") or []
    if not deny:
        return []
    blob = _detail_text_blob(payload)
    rejections: List[Rejection] = []
    for kw in deny:
        if not kw:
            continue
        if kw in blob:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="detail:description",
                    reason="category_mismatch",
                    detail=(
                        f"category='{spec.category}' 인데 detail 본문에 "
                        f"금기 키워드 '{kw}' 포함"
                    ),
                    instruction=(
                        f"'{kw}' 표현을 빼고 '{spec.category}' 에 어울리는 활동 설명으로 "
                        "description/progress_style 을 다시 작성하라."
                    ),
                )
            )
    return rejections


# ---------------------------------------------------------------------------
# Rule 3. cost_breakdown 총액
# ---------------------------------------------------------------------------


def rule_cost_breakdown_total(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """sum(cost_breakdown.amount) 이 expected_cost_per_person × (low~high) 범위인지."""
    expected = spec.budget.expected_cost_per_person
    if expected <= 0:
        return []
    cfg = rules.get("detail") or {}
    low_mult = float(cfg.get("cost_total_tolerance_low", 0.7))
    high_mult = float(cfg.get("cost_total_tolerance_high", 1.5))
    low = expected * low_mult
    high = expected * high_mult

    rows = payload.get("cost_breakdown")
    if not isinstance(rows, list) or not rows:
        return []
    total = 0
    for row in rows:
        if isinstance(row, dict):
            amt = row.get("amount")
            if isinstance(amt, (int, float)):
                total += int(amt)
    if total < low or total > high:
        return [
            Rejection(
                layer="rule",
                rejected_field="detail:cost_breakdown",
                reason="cost_total_out_of_range",
                detail=(
                    f"cost_breakdown 합계 {total}원이 허용 범위 "
                    f"{int(low)}~{int(high)}원 (expected={expected}) 밖"
                ),
                instruction=(
                    f"cost_breakdown 항목들의 amount 합이 약 {expected}원 기준 "
                    f"{int(low)}~{int(high)}원 사이가 되도록 조정하라."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Rule 4. host_intro 최소 길이 (supporter_required 시)
# ---------------------------------------------------------------------------


def rule_host_intro_length(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """supporter_required=True 이면 host_intro ≥ N자."""
    if not spec.activity_constraints.supporter_required:
        return []
    cfg = rules.get("detail") or {}
    min_len = int(cfg.get("host_intro_min_length_when_supporter", 60))
    intro = payload.get("host_intro")
    if not isinstance(intro, str):
        return [
            Rejection(
                layer="rule",
                rejected_field="detail:host_intro",
                reason="host_intro_missing",
                detail="supporter_required=True 인데 host_intro 가 문자열 아님",
                instruction=(
                    f"host_intro 에 supporter('{spec.host_persona.type}') 톤을 담은 "
                    f"{min_len}자 이상의 자기소개를 작성하라."
                ),
            )
        ]
    if len(intro) < min_len:
        return [
            Rejection(
                layer="rule",
                rejected_field="detail:host_intro",
                reason="host_intro_too_short",
                detail=f"host_intro 길이 {len(intro)} < {min_len}",
                instruction=(
                    f"host_intro 를 {min_len}자 이상으로 확장하라. "
                    f"'{spec.host_persona.type}' 호스트 경험, 동네/카테고리 선호, "
                    "처음 오는 사람을 위한 안내를 포함하면 길이를 맞추기 쉽다."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Rule 5. policy_notes 금기어
# ---------------------------------------------------------------------------


def rule_policy_notes_safe(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """policy_notes 에 '환불 불가' / '계약' / '법적' 등 금지어 없음."""
    notes = payload.get("policy_notes")
    if not isinstance(notes, str) or not notes.strip():
        return []  # optional 필드 — 없으면 skip.
    cfg = rules.get("detail") or {}
    forbidden = cfg.get("policy_forbidden_terms") or []
    rejections: List[Rejection] = []
    for term in forbidden:
        if term and term in notes:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="detail:policy_notes",
                    reason="policy_notes_forbidden_term",
                    detail=f"policy_notes 에 금지어 '{term}' 포함",
                    instruction=(
                        f"policy_notes 에서 '{term}' 같은 법적/계약성 표현을 제거하고, "
                        "모임 운영 관례(노쇼 시 다음 모집 제한 등)로 다시 작성하라."
                    ),
                )
            )
    return rejections


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

_DETAIL_RULE_FUNCTIONS = (
    rule_description_sentence_count,
    rule_category_consistency_detail,
    rule_cost_breakdown_total,
    rule_host_intro_length,
    rule_policy_notes_safe,
)


def validate_detail_rules(
    payload: Dict[str, Any],
    spec: ContentSpec,
    *,
    rules: Optional[Dict[str, Any]] = None,
    rules_dir: Optional[Path] = None,
) -> ValidationResult:
    """SpotDetail 5개 rule 일괄 실행."""
    if rules is None:
        rules = load_detail_rules(rules_dir)

    all_rejections: List[Rejection] = []
    rule_stats: Dict[str, int] = {}
    for fn in _DETAIL_RULE_FUNCTIONS:
        out = fn(payload, spec, rules)
        rule_stats[fn.__name__] = len(out)
        all_rejections.extend(out)

    meta = {
        "rule_stats": rule_stats,
        "category": spec.category,
    }
    return ValidationResult.from_rejections("rule", all_rejections, meta=meta)


__all__ = [
    "validate_detail_rules",
    "load_detail_rules",
    "rule_description_sentence_count",
    "rule_category_consistency_detail",
    "rule_cost_breakdown_total",
    "rule_host_intro_length",
    "rule_policy_notes_safe",
]
