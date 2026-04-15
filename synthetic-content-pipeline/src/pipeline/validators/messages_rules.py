"""Layer 2 — SpotCommunicationSnippets (messages) 전용 rule validation.

구현 rule:
    1. rule_snippets_all_present     — 4 snippet 모두 존재 (schema 안전망)
    2. rule_host_tone_consistency    — 4 snippet 에서 호스트 이름/1인칭 일관성 (warn)
    3. rule_recruit_status_match     — feed.status=recruiting 일 때 recruiting_intro 에 모집 어휘
    4. rule_forbidden_phrases        — "환불/위약금/법적" 등 금기어
    5. rule_day_of_notice_has_time   — day_of_notice 에 시간 표현 (HH:MM or 'N시')

rule_recruit_status_match 는 spec.activity_result 유무로 recruit 상태를 간접 추정한다
(activity_result=None → recruiting). feed.status 를 직접 받고 싶으면 cross_reference.py
쪽에서 교차 검증한다 (이 모듈은 단일 content type 범위).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from pipeline.spec.models import ContentSpec
from pipeline.validators.types import Rejection, ValidationResult

DEFAULT_RULES_DIR = Path("config/rules")

SNIPPET_KEYS = ("recruiting_intro", "join_approval", "day_of_notice", "post_thanks")

#: 1인칭 / 호스트 호칭 계열 — 톤 일관성을 판단할 때 최소 1개 등장해야 한다.
_HOST_TONE_HINTS = (
    "저",
    "제가",
    "저희",
    "호스트",
    "supporter",
    "드릴",
    "드려요",
)

#: day_of_notice 시간 표현 패턴 (HH:MM 또는 N시).
_TIME_RE = re.compile(r"(\d{1,2}\s*:\s*\d{2}|\d{1,2}\s*시)")


def load_messages_rules(rules_dir: Optional[Path] = None) -> Dict[str, Any]:
    base = Path(rules_dir) if rules_dir else DEFAULT_RULES_DIR
    path = base / "messages_rules.yaml"
    data: Dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    return {"messages": data}


def _is_recruiting(spec: ContentSpec) -> bool:
    """activity_result 가 없으면 recruiting 으로 간주."""
    return spec.activity_result is None


def _snippets_blob(payload: Dict[str, Any]) -> str:
    parts: List[str] = []
    for k in SNIPPET_KEYS:
        v = payload.get(k)
        if isinstance(v, str):
            parts.append(v)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Rule 1. 4 snippet 모두 존재
# ---------------------------------------------------------------------------


def rule_snippets_all_present(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    rejections: List[Rejection] = []
    for k in SNIPPET_KEYS:
        v = payload.get(k)
        if not isinstance(v, str) or not v.strip():
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field=f"messages:{k}",
                    reason="snippet_missing",
                    detail=f"messages.{k} 이 null 또는 빈 문자열",
                    instruction=(
                        f"messages.{k} 에 해당 단계 메시지를 다시 작성해 포함하라."
                    ),
                )
            )
    return rejections


# ---------------------------------------------------------------------------
# Rule 2. 호스트 톤 일관성 (warn)
# ---------------------------------------------------------------------------


def rule_host_tone_consistency(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """4 snippet 전체에 1인칭/호스트 호칭이 한 번도 등장하지 않으면 warn."""
    blob = _snippets_blob(payload)
    if not blob:
        return []
    hit = any(hint in blob for hint in _HOST_TONE_HINTS)
    if hit:
        return []
    return [
        Rejection(
            layer="rule",
            rejected_field="messages:host_tone",
            reason="host_tone_inconsistent",
            detail="4 snippet 전체에 호스트 1인칭/호칭 표현이 없음",
            instruction=(
                "4 snippet 을 동일한 호스트 톤으로 다시 작성하라. "
                "'저', '제가', '저희', '호스트' 중 자연스러운 표현을 "
                "최소 한 곳 이상 사용해 1인 화자 톤을 유지하라."
            ),
            severity="warn",
        )
    ]


# ---------------------------------------------------------------------------
# Rule 3. recruiting 상태에서 모집 어휘 존재
# ---------------------------------------------------------------------------


def rule_recruit_status_match(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    if not _is_recruiting(spec):
        return []
    intro = payload.get("recruiting_intro")
    if not isinstance(intro, str) or not intro.strip():
        return []  # rule 1 에서 이미 잡힘.
    cfg = rules.get("messages") or {}
    keywords = cfg.get("recruit_intent_keywords") or []
    if not keywords:
        return []
    hit = any(kw in intro for kw in keywords)
    if hit:
        return []
    return [
        Rejection(
            layer="rule",
            rejected_field="messages:recruiting_intro",
            reason="recruit_intent_missing",
            detail=(
                "spec.activity_result 가 없어 recruiting 상태인데 "
                "recruiting_intro 에 모집 어휘가 없음"
            ),
            instruction=(
                "recruiting_intro 에 '모집', '참여', '신청', '함께해' 중 "
                "자연스러운 모집 표현을 포함해서 다시 작성하라."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Rule 4. 금기 문구
# ---------------------------------------------------------------------------


def rule_forbidden_phrases(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    cfg = rules.get("messages") or {}
    forbidden = cfg.get("forbidden_phrases") or []
    rejections: List[Rejection] = []
    for k in SNIPPET_KEYS:
        v = payload.get(k)
        if not isinstance(v, str):
            continue
        for term in forbidden:
            if term and term in v:
                rejections.append(
                    Rejection(
                        layer="rule",
                        rejected_field=f"messages:{k}",
                        reason="messages_forbidden_phrase",
                        detail=f"messages.{k} 에 금기어 '{term}' 포함",
                        instruction=(
                            f"messages.{k} 에서 '{term}' 같은 법적/계약성 표현을 제거하고, "
                            "모임 운영 톤에 맞는 문구로 바꿔라."
                        ),
                    )
                )
    return rejections


# ---------------------------------------------------------------------------
# Rule 5. day_of_notice 시간 표현
# ---------------------------------------------------------------------------


def rule_day_of_notice_has_time(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    notice = payload.get("day_of_notice")
    if not isinstance(notice, str) or not notice.strip():
        return []
    if _TIME_RE.search(notice):
        return []
    return [
        Rejection(
            layer="rule",
            rejected_field="messages:day_of_notice",
            reason="day_of_notice_no_time",
            detail="day_of_notice 에 시간 표현(HH:MM 또는 'N시')이 없음",
            instruction=(
                f"day_of_notice 에 '{spec.schedule.start_time}' 같은 구체 시각을 포함해 "
                "당일 집결 안내 문구를 보강하라."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

_MESSAGES_RULE_FUNCTIONS = (
    rule_snippets_all_present,
    rule_host_tone_consistency,
    rule_recruit_status_match,
    rule_forbidden_phrases,
    rule_day_of_notice_has_time,
)


def validate_messages_rules(
    payload: Dict[str, Any],
    spec: ContentSpec,
    *,
    rules: Optional[Dict[str, Any]] = None,
    rules_dir: Optional[Path] = None,
) -> ValidationResult:
    if rules is None:
        rules = load_messages_rules(rules_dir)

    all_rejections: List[Rejection] = []
    rule_stats: Dict[str, int] = {}
    for fn in _MESSAGES_RULE_FUNCTIONS:
        out = fn(payload, spec, rules)
        rule_stats[fn.__name__] = len(out)
        all_rejections.extend(out)

    meta = {
        "rule_stats": rule_stats,
        "recruit_state": "recruiting" if _is_recruiting(spec) else "closed",
    }
    return ValidationResult.from_rejections("rule", all_rejections, meta=meta)


__all__ = [
    "validate_messages_rules",
    "load_messages_rules",
    "rule_snippets_all_present",
    "rule_host_tone_consistency",
    "rule_recruit_status_match",
    "rule_forbidden_phrases",
    "rule_day_of_notice_has_time",
]
