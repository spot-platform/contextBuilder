"""Layer 2 — Rule Validation (deterministic).

플랜 §5 Layer 2 표 8개 규칙을 함수화한다. **LLM 호출 금지** — 순수 Python.

각 rule 함수 시그니처:
    rule_xxx(payload: dict, spec: ContentSpec, rules: dict) -> list[Rejection]

규칙 매핑 (rule_table.md 도 동기화 필수):
    1. rule_region_consistency        — 지역명 누락
    2. rule_category_consistency      — 카테고리 deny 키워드
    3. rule_price_consistency         — 금액 ±tolerance 범위
    4. rule_time_consistency          — 심야인데 아침 키워드
    5. rule_target_consistency        — beginner_friendly인데 숙련자 표현
    6. rule_host_consistency          — supporter_required인데 supporter_label 비어있음
    7. rule_realism_budget            — 소규모 모임 1인 단가 상한
    8. rule_realism_duration          — duration 짧은데 "8시간/하루 종일"

rules 파라미터는 ``config/rules/feed_rules.yaml`` + ``shared_rules.yaml`` 을
``load_feed_rules()`` 로 머지한 dict.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from pipeline.spec.models import ContentSpec
from pipeline.validators.types import Rejection, ValidationResult

# ---------------------------------------------------------------------------
# 선택적 의존성 — rapidfuzz (없으면 difflib fallback)
# ---------------------------------------------------------------------------

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz  # type: ignore

    def _similarity(a: str, b: str) -> float:
        """0~100 범위 partial ratio. 한국어 짧은 문자열에 적합."""
        return float(_rapidfuzz_fuzz.partial_ratio(a, b))

    _SIMILARITY_BACKEND = "rapidfuzz"
except ImportError:  # pragma: no cover - fallback 경로
    from difflib import SequenceMatcher

    def _similarity(a: str, b: str) -> float:
        """0~100 범위 SequenceMatcher ratio."""
        return SequenceMatcher(None, a, b).ratio() * 100.0

    _SIMILARITY_BACKEND = "difflib"


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

#: rule yaml 디렉토리 (project root 기준 상대).
DEFAULT_RULES_DIR = Path("config/rules")

#: 지역 유사도 통과 임계 (rapidfuzz partial_ratio).
REGION_SIMILARITY_THRESHOLD = 75.0

#: 시간대 → 카테고리별 금기 키워드 (Layer 2 시간 일관성).
NIGHT_FORBIDDEN_KEYWORDS = ("아침", "오전", "햇살", "이른 오후", "브런치")
MORNING_FORBIDDEN_KEYWORDS = ("야식", "심야", "새벽")

#: 대상(beginner_friendly) 위반 키워드.
ADVANCED_ONLY_KEYWORDS = (
    "숙련자",
    "경험자 위주",
    "고급반",
    "프로 전용",
    "초보 사양",
    "초보 사절",
)

#: 빈 supporter_label 로 간주할 값.
EMPTY_SUPPORTER_VALUES = {"", "none", "None", "null", "n/a", "N/A", "없음"}

#: Phase Peer-E: 프로 강사 톤 금기어 fallback (shared_rules.yaml 이 없을 때).
DEFAULT_FORBIDDEN_PRO_KEYWORDS = (
    "강좌",
    "강사",
    "수강생",
    "수강료",
    "강의료",
    "강사료",
    "자격증",
    "원데이 클래스",
    "원데이클래스",
    "정규 수업",
    "정규수업",
    "개설하여",
)


# ---------------------------------------------------------------------------
# 시간/금액 유틸
# ---------------------------------------------------------------------------

#: "1인 1.5~2만원", "참가비 23,000원", "20000원" 등에서 숫자 추출.
_PRICE_NUMBER_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>만원|천원|원)?",
)


def _parse_price_label(label: str) -> List[int]:
    """price_label 에서 숫자(원 단위) 추출. 범위 표기 ``1.5~2만원`` 도 지원."""
    if not label:
        return []
    label = label.replace(",", "")
    out: List[int] = []
    for m in _PRICE_NUMBER_RE.finditer(label):
        try:
            num = float(m.group("num"))
        except ValueError:
            continue
        unit = m.group("unit") or ""
        if unit == "만원":
            out.append(int(round(num * 10_000)))
        elif unit == "천원":
            out.append(int(round(num * 1_000)))
        elif unit == "원":
            out.append(int(round(num)))
        else:
            # unit 누락 — 직전 숫자 단위 상속이 어렵기 때문에 큰 수만 채택.
            if num >= 1000:
                out.append(int(round(num)))
    return out


def _slot_from_start_time(start_time: str) -> str:
    """HH:MM → ``"morning" | "afternoon" | "evening" | "night"``."""
    try:
        hh = int(start_time.split(":")[0])
    except (ValueError, AttributeError):
        return "unknown"
    if 5 <= hh < 11:
        return "morning"
    if 11 <= hh < 17:
        return "afternoon"
    if 17 <= hh < 22:
        return "evening"
    return "night"


def _payload_text_blob(payload: Dict[str, Any]) -> str:
    """지역/카테고리 키워드 검색을 위한 통합 텍스트."""
    parts: List[str] = []
    for key in ("title", "summary", "region_label", "time_label", "supporter_label"):
        v = payload.get(key)
        if isinstance(v, str):
            parts.append(v)
    tags = payload.get("tags")
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Rules YAML loader
# ---------------------------------------------------------------------------


def load_feed_rules(rules_dir: Optional[Path] = None) -> Dict[str, Any]:
    """``config/rules/feed_rules.yaml`` + ``shared_rules.yaml`` 머지 로드.

    Args:
        rules_dir: rules yaml 디렉토리. None이면 DEFAULT_RULES_DIR.

    Returns:
        ``{"categories": {...}, "shared": {...}, "feed": {...}}`` dict.
        파일 부재 시 빈 dict 키로 채워서 안전 동작 (모든 rule이 통과 쪽).
    """
    base = Path(rules_dir) if rules_dir else DEFAULT_RULES_DIR
    feed_path = base / "feed_rules.yaml"
    shared_path = base / "shared_rules.yaml"

    feed_yaml: Dict[str, Any] = {}
    shared_yaml: Dict[str, Any] = {}
    if feed_path.exists():
        with feed_path.open("r", encoding="utf-8") as fh:
            feed_yaml = yaml.safe_load(fh) or {}
    if shared_path.exists():
        with shared_path.open("r", encoding="utf-8") as fh:
            shared_yaml = yaml.safe_load(fh) or {}

    return {
        "categories": feed_yaml.get("categories", {}),
        "forbidden_long_duration_phrases": feed_yaml.get(
            "forbidden_long_duration_phrases", []
        ),
        "shared": shared_yaml,
        "feed": feed_yaml,
    }


# ---------------------------------------------------------------------------
# Rule 1. 지역 일관성
# ---------------------------------------------------------------------------


def rule_region_consistency(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """payload 본문에 spec.region 지역명이 등장하는지 확인.

    Reject 조건:
        - region_label / title / summary / tags 통합 텍스트 안에서
          spec.region 핵심 토큰의 partial similarity < REGION_SIMILARITY_THRESHOLD
    """
    region = (spec.region or "").strip()
    if not region or region == "알 수 없음":
        return []
    blob = _payload_text_blob(payload)
    if not blob:
        return [
            Rejection(
                layer="rule",
                rejected_field="region_label",
                reason="region_missing_text",
                detail="payload에 지역을 표기할 텍스트가 비어있음",
                instruction=f"region_label 또는 title/summary 어딘가에 '{region}' 을 명시하라.",
            )
        ]
    sim = _similarity(region, blob)
    # 핵심 토큰(공백 split 마지막) 도 별도 체크 — "수원시 연무동" → "연무동".
    last_token = region.split()[-1]
    sim_last = _similarity(last_token, blob)
    best = max(sim, sim_last)
    if best < REGION_SIMILARITY_THRESHOLD:
        return [
            Rejection(
                layer="rule",
                rejected_field="region_label",
                reason="region_mismatch",
                detail=(
                    f"spec.region='{region}' 가 payload 본문에 충분히 등장하지 않음 "
                    f"(best similarity={best:.1f} < {REGION_SIMILARITY_THRESHOLD})"
                ),
                instruction=(
                    f"region_label 을 정확히 '{region}' 으로 두고, "
                    f"title 또는 summary에도 '{last_token}' 단어를 포함하라."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Rule 2. 카테고리 일관성
# ---------------------------------------------------------------------------


def rule_category_consistency(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """payload 본문에 spec.category 의 deny 키워드가 있으면 reject.

    rules 구조:
        rules["categories"][category]["deny_keywords"]: list[str]
    """
    cats = rules.get("categories") or {}
    cat_rules = cats.get(spec.category) or {}
    deny = cat_rules.get("deny_keywords") or []
    if not deny:
        return []
    blob = _payload_text_blob(payload)
    rejections: List[Rejection] = []
    for kw in deny:
        if not kw:
            continue
        if kw in blob:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="summary",
                    reason="category_mismatch",
                    detail=(
                        f"category='{spec.category}' 인데 금기 키워드 '{kw}' 가 본문에 포함됨"
                    ),
                    instruction=(
                        f"'{kw}' 단어를 빼고, '{spec.category}' 카테고리에 어울리는 표현으로 "
                        "다시 작성하라."
                    ),
                )
            )
    return rejections


# ---------------------------------------------------------------------------
# Rule 3. 금액 일관성
# ---------------------------------------------------------------------------


def rule_price_consistency(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """price_label 숫자가 expected_cost_per_person * (low~high) 범위 내인지."""
    label = payload.get("price_label")
    if not isinstance(label, str) or not label.strip():
        return []
    expected = spec.budget.expected_cost_per_person
    if expected <= 0:
        return []
    shared = rules.get("shared") or {}
    low_mult = float(shared.get("price_tolerance_low", 0.5))
    high_mult = float(shared.get("price_tolerance_high", 2.5))
    low = expected * low_mult
    high = expected * high_mult

    parsed = _parse_price_label(label)
    if not parsed:
        # 숫자 추출 실패 — 경고만 (text-only label)
        return [
            Rejection(
                layer="rule",
                rejected_field="price_label",
                reason="price_unparseable",
                detail=f"price_label='{label}' 에서 금액 숫자를 추출하지 못함",
                instruction="price_label 에 '1인 1.5~2만원' 처럼 숫자+단위 표기를 포함하라.",
                severity="warn",
            )
        ]
    rejections: List[Rejection] = []
    for amount in parsed:
        if amount < low or amount > high:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="price_label",
                    reason="price_out_of_range",
                    detail=(
                        f"price_label 추출액 {amount}원이 허용 범위 "
                        f"{int(low)}~{int(high)}원 (expected={expected}) 밖"
                    ),
                    instruction=(
                        f"price_label 을 1인 약 {expected}원 기준 "
                        f"{int(low)}~{int(high)}원 사이로 표기하라."
                    ),
                )
            )
    return rejections


# ---------------------------------------------------------------------------
# Rule 4. 시간 일관성
# ---------------------------------------------------------------------------


def rule_time_consistency(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """spec.schedule.start_time 시간대와 payload 본문 키워드가 정합하는지."""
    slot = _slot_from_start_time(spec.schedule.start_time)
    blob = _payload_text_blob(payload)
    if not blob:
        return []

    rejections: List[Rejection] = []
    if slot == "night":
        for kw in NIGHT_FORBIDDEN_KEYWORDS:
            if kw in blob:
                rejections.append(
                    Rejection(
                        layer="rule",
                        rejected_field="summary",
                        reason="time_mismatch_night",
                        detail=(
                            f"start_time={spec.schedule.start_time} (night)인데 "
                            f"본문에 '{kw}' 표현 포함"
                        ),
                        instruction=(
                            f"'{kw}' 표현을 빼고 저녁/밤 분위기에 어울리는 표현으로 "
                            "교체하라."
                        ),
                    )
                )
    if slot == "morning":
        for kw in MORNING_FORBIDDEN_KEYWORDS:
            if kw in blob:
                rejections.append(
                    Rejection(
                        layer="rule",
                        rejected_field="summary",
                        reason="time_mismatch_morning",
                        detail=(
                            f"start_time={spec.schedule.start_time} (morning)인데 "
                            f"본문에 '{kw}' 표현 포함"
                        ),
                        instruction=(
                            f"'{kw}' 표현을 빼고 아침/오전 분위기에 어울리는 표현으로 "
                            "교체하라."
                        ),
                    )
                )
    return rejections


# ---------------------------------------------------------------------------
# Rule 5. 대상 일관성
# ---------------------------------------------------------------------------


def rule_target_consistency(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """beginner_friendly=True 인데 숙련자 전용 표현 사용 시 reject."""
    if not spec.activity_constraints.beginner_friendly:
        return []
    blob = _payload_text_blob(payload)
    rejections: List[Rejection] = []
    for kw in ADVANCED_ONLY_KEYWORDS:
        if kw in blob:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="summary",
                    reason="target_mismatch_beginner",
                    detail=(
                        f"beginner_friendly=True 인데 본문에 숙련자 전용 표현 '{kw}' 포함"
                    ),
                    instruction=(
                        f"'{kw}' 같은 숙련자 전용 톤을 빼고 초면/초보도 환영하는 톤으로 "
                        "다시 작성하라."
                    ),
                )
            )
    return rejections


# ---------------------------------------------------------------------------
# Rule 6. 호스트 일관성
# ---------------------------------------------------------------------------


def rule_host_consistency(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """supporter_required=True 인데 supporter_label 이 비어있거나 'none' 류면 reject."""
    if not spec.activity_constraints.supporter_required:
        return []
    label = payload.get("supporter_label")
    if not isinstance(label, str):
        return [
            Rejection(
                layer="rule",
                rejected_field="supporter_label",
                reason="host_label_missing",
                detail="supporter_required=True 인데 supporter_label 가 None 또는 문자열 아님",
                instruction=(
                    f"supporter_label 에 '{spec.host_persona.type}' 같은 supporter 카테고리 "
                    "라벨을 채워라."
                ),
            )
        ]
    if label.strip() in EMPTY_SUPPORTER_VALUES:
        return [
            Rejection(
                layer="rule",
                rejected_field="supporter_label",
                reason="host_label_empty",
                detail=f"supporter_label='{label}' 가 빈/none 값",
                instruction=(
                    f"supporter_label 을 '{spec.host_persona.type}' 같은 supporter 라벨로 "
                    "채워라."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Rule 7. 현실성 — 소규모 모임 1인 단가 상한
# ---------------------------------------------------------------------------


def rule_realism_budget(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """expected_count <= small_group_threshold 일 때 1인 단가가 상한을 넘으면 reject."""
    shared = rules.get("shared") or {}
    threshold = int(shared.get("small_group_threshold", 6))
    max_per_person = int(shared.get("max_per_person_small_group", 50_000))
    if spec.participants.expected_count > threshold:
        return []

    label = payload.get("price_label")
    if not isinstance(label, str) or not label.strip():
        return []
    parsed = _parse_price_label(label)
    if not parsed:
        return []
    over = [a for a in parsed if a > max_per_person]
    if not over:
        return []
    return [
        Rejection(
            layer="rule",
            rejected_field="price_label",
            reason="realism_budget_too_high",
            detail=(
                f"소규모 모임(expected_count={spec.participants.expected_count} "
                f"≤ {threshold})에 1인 {max(over)}원 표기 (상한 {max_per_person}원 초과)"
            ),
            instruction=(
                f"소규모 모임이므로 1인 단가를 {max_per_person}원 이하로 다시 표기하라."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Rule 8. 현실성 — duration 짧은데 "8시간/하루 종일"
# ---------------------------------------------------------------------------


def rule_realism_duration(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """spec.schedule.duration_minutes 짧은데 본문에 장시간 표현 사용 시 reject."""
    forbidden = rules.get("forbidden_long_duration_phrases") or []
    if not forbidden:
        return []
    duration = spec.schedule.duration_minutes
    # 4시간(240분) 이하 모임에서는 "8시간 / 하루 종일" 같은 표현 금지.
    if duration > 240:
        return []
    blob = _payload_text_blob(payload)
    rejections: List[Rejection] = []
    for phrase in forbidden:
        if phrase and phrase in blob:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="summary",
                    reason="realism_duration_too_long",
                    detail=(
                        f"duration_minutes={duration} 인데 본문에 '{phrase}' 표현 포함"
                    ),
                    instruction=(
                        f"'{phrase}' 표현을 빼고 약 {duration}분(≈{duration//60}시간) "
                        "분량으로 표현하라."
                    ),
                )
            )
    return rejections


# ---------------------------------------------------------------------------
# Rule 9. Phase Peer-E — 프로 강사 톤 금지 (또래 강사 marketplace 컨셉 위반)
# ---------------------------------------------------------------------------


def rule_no_pro_keywords(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """feed / detail / messages 등 공개 콘텐츠에 프로 강사 어휘가 있으면 reject.

    peer pivot plan §5 Phase E — 이 앱은 또래 강사 marketplace 이므로 "강좌/
    수강생/수강료/자격증/원데이 클래스" 같은 프로 어휘는 제품 DNA 위반이다.

    rules 구조::

        rules["shared"]["forbidden_pro_keywords"]: list[str]

    fallback 은 DEFAULT_FORBIDDEN_PRO_KEYWORDS.
    """
    shared = rules.get("shared") or {}
    forbidden = shared.get("forbidden_pro_keywords") or list(
        DEFAULT_FORBIDDEN_PRO_KEYWORDS
    )
    if not forbidden:
        return []
    blob = _payload_text_blob(payload)
    if not blob:
        return []
    rejections: List[Rejection] = []
    for kw in forbidden:
        if not kw:
            continue
        if kw in blob:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="summary",
                    reason="peer_tone_pro_keyword",
                    detail=(
                        f"프로 강사 어휘 '{kw}' 가 포함되어 있다. "
                        "또래 강사 marketplace 컨셉 위반."
                    ),
                    instruction=(
                        f"'{kw}' 를 제거하고 또래 톤으로 다시 작성하라. "
                        "수업→모임, 강사→호스트, 수강생→참가자, 수강료→참가비."
                    ),
                )
            )
    return rejections


# ---------------------------------------------------------------------------
# Public entry — feed 전용 Layer 2 종합 검증
# ---------------------------------------------------------------------------


_FEED_RULE_FUNCTIONS = (
    rule_region_consistency,
    rule_category_consistency,
    rule_price_consistency,
    rule_time_consistency,
    rule_target_consistency,
    rule_host_consistency,
    rule_realism_budget,
    rule_realism_duration,
    # Phase Peer-E 신규
    rule_no_pro_keywords,
)


def validate_feed_rules(
    payload: Dict[str, Any],
    spec: ContentSpec,
    *,
    rules: Optional[Dict[str, Any]] = None,
    rules_dir: Optional[Path] = None,
) -> ValidationResult:
    """8개 deterministic rule을 모두 실행하고 ValidationResult로 합성.

    Args:
        payload: feed 콘텐츠 dict.
        spec: ContentSpec (builder 출력).
        rules: 미리 로드된 rules dict. None이면 ``load_feed_rules`` 호출.
        rules_dir: rules yaml 디렉토리 override.

    Returns:
        ValidationResult(layer="rule", ...).
    """
    if rules is None:
        rules = load_feed_rules(rules_dir)

    all_rejections: List[Rejection] = []
    rule_stats: Dict[str, int] = {}
    for fn in _FEED_RULE_FUNCTIONS:
        out = fn(payload, spec, rules)
        rule_stats[fn.__name__] = len(out)
        all_rejections.extend(out)

    meta = {
        "similarity_backend": _SIMILARITY_BACKEND,
        "rule_stats": rule_stats,
        "category": spec.category,
        "region": spec.region,
    }
    return ValidationResult.from_rejections("rule", all_rejections, meta=meta)


__all__ = [
    "validate_feed_rules",
    "load_feed_rules",
    "rule_region_consistency",
    "rule_category_consistency",
    "rule_price_consistency",
    "rule_time_consistency",
    "rule_target_consistency",
    "rule_host_consistency",
    "rule_realism_budget",
    "rule_realism_duration",
    "rule_no_pro_keywords",
    "REGION_SIMILARITY_THRESHOLD",
    "NIGHT_FORBIDDEN_KEYWORDS",
    "MORNING_FORBIDDEN_KEYWORDS",
    "ADVANCED_ONLY_KEYWORDS",
    "DEFAULT_FORBIDDEN_PRO_KEYWORDS",
]
