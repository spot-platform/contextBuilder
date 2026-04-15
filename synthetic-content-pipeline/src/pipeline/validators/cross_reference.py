"""Layer 3 — Cross-Reference Validation (스팟 단위).

플랜 §5 Layer 3 표를 1:1 로 구현한다. 5개 content type (feed/detail/plan/
messages/review) 이 **한 스팟 안에서 모순되지 않는지** 를 deterministic 하게
체크한다.

구현된 5 쌍:

1. feed ↔ detail     — 인원수 / 금액 / 카테고리 / 지역 정합
2. detail ↔ plan     — 활동 키워드 / 자료 정합 (약한 경고 포함)
3. detail ↔ review   — 활동 종류 (카테고리 키워드) 모순 없음
4. feed ↔ messages   — 모집 상태 / 시각 정합
5. review ↔ activity_result — 노쇼/참여 정합, sentiment 정합

설계 포인트:
    - rejection 의 ``rejected_field`` 는 반드시 ``"<content_type>:<field>"`` 형태
      (재시도 루프가 어느 content 를 regenerate 해야 하는지 식별).
    - spot bundle 에 일부 content 만 있으면 해당 pair 를 skip 하고
      ``meta["skipped_pairs"]`` 에 기록한다.
    - LLM 호출 금지. rapidfuzz 없으면 difflib fallback (rules.py 와 동일 backend).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from pipeline.spec.models import ContentSpec
from pipeline.validators.types import Rejection, ValidationResult

# ---------------------------------------------------------------------------
# 유사도 backend — rules.py 와 동일한 선택적 의존성 전략
# ---------------------------------------------------------------------------

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz  # type: ignore

    def _similarity(a: str, b: str) -> float:
        return float(_rapidfuzz_fuzz.partial_ratio(a, b))

    _SIMILARITY_BACKEND = "rapidfuzz"
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    def _similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0

    _SIMILARITY_BACKEND = "difflib"


DEFAULT_RULES_DIR = Path("config/rules")

# price_label 에서 숫자 추출 — rules.py 의 _parse_price_label 와 동일 정규식.
_PRICE_NUMBER_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>만원|천원|원)?",
)

# time_label / day_of_notice 시각 추출 — "19:00", "7시", "오후 7시", "저녁 7시" 등.
_TIME_HHMM_RE = re.compile(r"(\d{1,2})\s*:\s*(\d{2})")
_TIME_HOUR_RE = re.compile(r"(오전|오후|아침|점심|저녁|밤|새벽)?\s*(\d{1,2})\s*시")

#: 상대 표현 → hh 보정.
_TIME_PREFIX_PM = {"오후", "저녁", "밤"}
_TIME_PREFIX_AM = {"오전", "아침", "새벽"}


def load_cross_reference_rules(rules_dir: Optional[Path] = None) -> Dict[str, Any]:
    """``cross_reference.yaml`` 로드."""
    base = Path(rules_dir) if rules_dir else DEFAULT_RULES_DIR
    path = base / "cross_reference.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# 공용 파서
# ---------------------------------------------------------------------------


def _parse_price_numbers(label: str) -> List[int]:
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
            if num >= 1000:
                out.append(int(round(num)))
    return out


def _parse_time_minutes(text: str) -> List[int]:
    """본문에서 'HH:MM' 또는 'N시' 를 모두 찾아 분 단위 리스트로 반환.

    '오후 7시' 는 19시로 해석, '오전' 표기는 그대로 hh. 그 외 (암묵적) 는
    **hh 값만** 반환하므로 호출자가 ±12 range 허용 로직으로 감싸야 한다.
    """
    minutes: List[int] = []
    for m in _TIME_HHMM_RE.finditer(text):
        hh = int(m.group(1))
        mm = int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            minutes.append(hh * 60 + mm)
    for m in _TIME_HOUR_RE.finditer(text):
        prefix = m.group(1)
        hh = int(m.group(2))
        if prefix in _TIME_PREFIX_PM and hh < 12:
            hh += 12
        if prefix in _TIME_PREFIX_AM and hh == 12:
            hh = 0
        if 0 <= hh <= 23:
            minutes.append(hh * 60)
    return minutes


def _category_hits(text: str, keywords: List[str]) -> List[str]:
    return [kw for kw in keywords if kw and kw in text]


# ---------------------------------------------------------------------------
# Pair 1. feed ↔ detail
# ---------------------------------------------------------------------------


def _pair_feed_detail(
    feed: Dict[str, Any],
    detail: Dict[str, Any],
    spec: ContentSpec,
    rules: Dict[str, Any],
) -> List[Rejection]:
    """feed/detail 간 금액·지역·카테고리 정합."""
    rejections: List[Rejection] = []
    cat_map: Dict[str, List[str]] = rules.get("category_keywords") or {}

    # 1-a. 금액: feed.price_label 의 숫자와 detail.cost_breakdown 합계가
    #      spec.budget.expected_cost_per_person 기준 허용 범위 안에서 정합하는지.
    expected = spec.budget.expected_cost_per_person
    if expected > 0:
        price_label = feed.get("price_label") or ""
        feed_nums = _parse_price_numbers(price_label) if isinstance(price_label, str) else []
        rows = detail.get("cost_breakdown") or []
        total = 0
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("amount"), (int, float)):
                    total += int(row["amount"])

        low = expected * float(rules.get("price_tolerance_low", 0.7))
        high = expected * float(rules.get("price_tolerance_high", 1.5))

        if feed_nums and total > 0:
            # feed 에 범위 표기가 있으면 평균값 사용.
            feed_center = sum(feed_nums) / len(feed_nums)
            # feed↔detail gap 이 expected 의 ±50% 를 넘으면 reject.
            if abs(feed_center - total) > expected * 0.5:
                rejections.append(
                    Rejection(
                        layer="cross_ref",
                        rejected_field="feed↔detail:price",
                        reason="price_mismatch",
                        detail=(
                            f"feed.price_label ≈ {int(feed_center)}원, "
                            f"detail.cost_breakdown 합계 = {total}원, "
                            f"spec expected={expected}원 — 격차 > 50%"
                        ),
                        instruction=(
                            "feed.price_label 과 detail.cost_breakdown 합계를 일치시켜라. "
                            f"기준값은 spec.budget.expected_cost_per_person={expected}원."
                        ),
                    )
                )
        # detail 합계가 feed 유무와 무관하게 아예 범위를 벗어나면 detail 만 지적.
        if total > 0 and (total < low or total > high):
            rejections.append(
                Rejection(
                    layer="cross_ref",
                    rejected_field="detail:cost_breakdown",
                    reason="cost_out_of_range_vs_spec",
                    detail=(
                        f"detail.cost_breakdown 합계 {total}원이 "
                        f"{int(low)}~{int(high)}원 (expected={expected}) 밖"
                    ),
                    instruction=(
                        f"detail.cost_breakdown 합계를 {int(low)}~{int(high)}원 "
                        "사이로 맞추어 재생성하라."
                    ),
                )
            )

    # 1-b. 지역: feed.region_label 토큰이 detail.title/description 안에 충분히 등장.
    region_label = feed.get("region_label")
    if isinstance(region_label, str) and region_label.strip():
        threshold = float(rules.get("fuzzy_threshold", 70))
        # 지역 마지막 토큰 — "수원시 연무동" → "연무동"
        last_token = region_label.strip().split()[-1]
        blob_parts: List[str] = []
        for k in ("title", "description", "target_audience"):
            v = detail.get(k)
            if isinstance(v, str):
                blob_parts.append(v)
        blob = " ".join(blob_parts)
        if blob:
            sim_full = _similarity(region_label, blob)
            sim_last = _similarity(last_token, blob)
            best = max(sim_full, sim_last)
            if best < threshold:
                rejections.append(
                    Rejection(
                        layer="cross_ref",
                        rejected_field="feed↔detail:region",
                        reason="region_mismatch",
                        detail=(
                            f"feed.region_label='{region_label}' 이 "
                            f"detail 본문에 충분히 등장하지 않음 (best={best:.1f} < {threshold})"
                        ),
                        instruction=(
                            f"detail.title 또는 description 에 '{last_token}' 지역명을 "
                            "최소 한 번 자연스럽게 언급하라."
                        ),
                    )
                )

    # 1-c. 카테고리: detail 본문에 spec.category 대표 키워드가 최소 1개 등장해야 함.
    cat_keywords = cat_map.get(spec.category) or []
    if cat_keywords:
        detail_blob_parts: List[str] = []
        for k in ("title", "description", "activity_purpose", "progress_style"):
            v = detail.get(k)
            if isinstance(v, str):
                detail_blob_parts.append(v)
        blob = " ".join(detail_blob_parts)
        min_hits = int(rules.get("min_category_keyword_hits", 1))
        hits = _category_hits(blob, cat_keywords)
        if len(hits) < min_hits:
            rejections.append(
                Rejection(
                    layer="cross_ref",
                    rejected_field="feed↔detail:category",
                    reason="category_mismatch",
                    detail=(
                        f"spec.category='{spec.category}' 대표 키워드 {cat_keywords} 중 "
                        f"detail 본문에서 {len(hits)}개만 검출 (요구 ≥{min_hits})"
                    ),
                    instruction=(
                        f"detail.description 에 '{spec.category}' 카테고리 대표어 "
                        f"({', '.join(cat_keywords[:4])}...) 중 하나를 자연스럽게 포함하라."
                    ),
                )
            )

    # 1-d. supporter 라벨: feed.supporter_label 과 detail.host_intro 호스트 타입 매칭.
    supporter_label = feed.get("supporter_label")
    host_intro = detail.get("host_intro")
    if (
        isinstance(supporter_label, str)
        and supporter_label.strip()
        and isinstance(host_intro, str)
        and host_intro.strip()
    ):
        # 핵심 토큰(supporter_xxx 또는 커스텀 라벨) 이 host_intro 에 등장하거나,
        # 유사도 ≥ threshold 이면 통과.
        threshold = float(rules.get("fuzzy_threshold", 70))
        sim = _similarity(supporter_label, host_intro)
        if supporter_label not in host_intro and sim < threshold:
            # warn: supporter_label 은 보통 'supporter_teacher' 같은 영문 타입인데
            # LLM 이 한국어 자연문으로 host_intro 를 쓰면 literal 매치가 거의 실패한다.
            # 점수 감점(diversity/persona_fit) 만으로 충분하고, 재생성 트리거는 안 건다.
            rejections.append(
                Rejection(
                    layer="cross_ref",
                    rejected_field="feed↔detail:supporter",
                    reason="supporter_label_mismatch",
                    detail=(
                        f"feed.supporter_label='{supporter_label}' 이 "
                        f"detail.host_intro 에 등장하지 않음 (sim={sim:.1f})"
                    ),
                    instruction=(
                        f"detail.host_intro 에 '{supporter_label}' 또는 같은 의미의 "
                        "호스트 타입 설명을 포함하라."
                    ),
                    severity="warn",
                )
            )

    return rejections


# ---------------------------------------------------------------------------
# Pair 2. detail ↔ plan
# ---------------------------------------------------------------------------


def _pair_detail_plan(
    detail: Dict[str, Any],
    plan: Dict[str, Any],
    spec: ContentSpec,
    rules: Dict[str, Any],
) -> List[Rejection]:
    """detail.activity_purpose / progress_style 의 키워드가 plan.steps 에 반영됐는지."""
    rejections: List[Rejection] = []
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return rejections

    # detail 핵심 어휘: activity_purpose + progress_style 합쳐 n-gram/단어 단위 체크는 과함 →
    # category_keywords 기반 공통 어휘 존재 여부로 대체.
    cat_map = rules.get("category_keywords") or {}
    cat_keywords = cat_map.get(spec.category) or []

    plan_blob_parts: List[str] = []
    for step in steps:
        if isinstance(step, dict):
            act = step.get("activity")
            if isinstance(act, str):
                plan_blob_parts.append(act)
    plan_blob = " ".join(plan_blob_parts)

    detail_blob_parts: List[str] = []
    for k in ("activity_purpose", "progress_style", "description"):
        v = detail.get(k)
        if isinstance(v, str):
            detail_blob_parts.append(v)
    detail_blob = " ".join(detail_blob_parts)

    if cat_keywords and plan_blob and detail_blob:
        detail_hits = set(_category_hits(detail_blob, cat_keywords))
        plan_hits = set(_category_hits(plan_blob, cat_keywords))
        overlap = detail_hits & plan_hits
        # detail 에 카테고리 어휘가 있었는데 plan 에 전혀 반영되지 않으면 reject.
        if detail_hits and not overlap:
            rejections.append(
                Rejection(
                    layer="cross_ref",
                    rejected_field="detail↔plan:activity",
                    reason="detail_plan_activity_mismatch",
                    detail=(
                        f"detail 카테고리 키워드 {sorted(detail_hits)} 중 "
                        f"plan.steps 에 반영된 항목이 없음"
                    ),
                    instruction=(
                        f"plan.steps[].activity 중 적어도 한 단계에 "
                        f"{', '.join(sorted(detail_hits))} 계열 활동을 포함하라."
                    ),
                )
            )

    # materials 약한 경고 — materials 에 특정 도구(러닝화 등)가 있는데 plan 에 아무 관련 활동이
    # 없으면 warn. materials 가 빈 배열이면 skip.
    materials = detail.get("materials")
    if isinstance(materials, list) and materials:
        unused: List[str] = []
        for item in materials:
            if not isinstance(item, str) or not item.strip():
                continue
            # 간단한 substring 매치. materials 아이템이 plan 어디에도 안 나오면 unused.
            if item not in plan_blob:
                unused.append(item)
        if unused and len(unused) == len(materials):
            # **모든** materials 가 plan 에 안 나올 때만 warn.
            rejections.append(
                Rejection(
                    layer="cross_ref",
                    rejected_field="detail↔plan:materials",
                    reason="materials_unused_in_plan",
                    detail=(
                        f"detail.materials={materials} 가 plan.steps 어디에도 "
                        "언급되지 않음"
                    ),
                    instruction=(
                        "plan.steps 중 한 단계에 materials 사용 장면을 추가하거나, "
                        "반대로 detail.materials 를 실제 사용하는 항목만 남겨라."
                    ),
                    severity="warn",
                )
            )

    return rejections


# ---------------------------------------------------------------------------
# Pair 3. detail ↔ review — 활동 종류 일치
# ---------------------------------------------------------------------------


def _pair_detail_review(
    detail: Dict[str, Any],
    review: Dict[str, Any],
    spec: ContentSpec,
    rules: Dict[str, Any],
) -> List[Rejection]:
    """review.review_text 가 detail 의 카테고리와 모순되지 않아야 한다.

    예: category=food 인데 review 에 "그림을 그렸어요" → reject.
    방법: review_text 에서 **다른 카테고리** 키워드가 spec.category 키워드보다 많이
    검출되면 모순으로 판정.
    """
    rejections: List[Rejection] = []
    text = review.get("review_text")
    if not isinstance(text, str) or not text.strip():
        return rejections

    cat_map: Dict[str, List[str]] = rules.get("category_keywords") or {}
    own_keywords = cat_map.get(spec.category) or []
    if not own_keywords:
        return rejections

    own_hits = _category_hits(text, own_keywords)

    # 다른 모든 카테고리의 키워드 히트 합산.
    other_hits: Dict[str, List[str]] = {}
    for other_cat, kws in cat_map.items():
        if other_cat == spec.category:
            continue
        hits = _category_hits(text, kws)
        if hits:
            other_hits[other_cat] = hits

    # 리뷰에 다른 카테고리 키워드가 2개 이상 있고, 자신 카테고리 키워드는 0개면 모순.
    total_other = sum(len(v) for v in other_hits.values())
    if total_other >= 2 and len(own_hits) == 0:
        dominant_cat = max(other_hits.items(), key=lambda kv: len(kv[1]))[0]
        rejections.append(
            Rejection(
                layer="cross_ref",
                rejected_field="detail↔review:activity_kind",
                reason="review_activity_kind_mismatch",
                detail=(
                    f"spec.category='{spec.category}' 인데 review_text 에 "
                    f"{dominant_cat} 키워드 {other_hits[dominant_cat]} 만 검출됨"
                ),
                instruction=(
                    f"review_text 를 '{spec.category}' 활동 경험으로 다시 작성하라 "
                    f"(참고: {', '.join(own_keywords[:4])} 등의 표현 활용)."
                ),
            )
        )

    return rejections


# ---------------------------------------------------------------------------
# Pair 4. feed ↔ messages — 모집 상태 & 시각
# ---------------------------------------------------------------------------


def _pair_feed_messages(
    feed: Dict[str, Any],
    messages: Dict[str, Any],
    spec: ContentSpec,
    rules: Dict[str, Any],
) -> List[Rejection]:
    rejections: List[Rejection] = []

    # 4-a. 모집 상태: feed.status='recruiting' 이면 recruiting_intro 에 모집 어휘 존재.
    status = feed.get("status")
    recruiting_intro = messages.get("recruiting_intro")
    if status == "recruiting" and isinstance(recruiting_intro, str) and recruiting_intro.strip():
        # 한국어 모집 표현 확장: 정중/친근/직접 표현 모두 허용.
        recruit_hints = (
            "모집", "참여", "신청", "함께해", "함께", "같이", "초대",
            "오세요", "뵙", "만나", "자리", "모임", "환영", "올리",
        )
        if not any(h in recruiting_intro for h in recruit_hints):
            # warn 으로 downgrade: hint 목록이 아무리 넓어도 LLM 문장 스타일에 따라
            # 0 매치가 가능하다. 점수 감점으로만 반영하고 재생성은 하지 않는다.
            rejections.append(
                Rejection(
                    layer="cross_ref",
                    rejected_field="feed↔messages:recruit_intent",
                    reason="recruit_intent_missing",
                    detail=(
                        "feed.status='recruiting' 인데 messages.recruiting_intro 에 "
                        "모집 의사 표현이 없음"
                    ),
                    instruction=(
                        "messages.recruiting_intro 에 '모집', '신청', '함께해' 등 "
                        "모집 어휘를 최소 한 번 포함해서 다시 작성하라."
                    ),
                    severity="warn",
                )
            )

    # 4-b. 시각: feed.time_label 에 시간이 표기되어 있으면 messages.day_of_notice 의 시각과
    #      ±time_tolerance_minutes 이내로 일치.
    time_label = feed.get("time_label")
    day_notice = messages.get("day_of_notice")
    if (
        isinstance(time_label, str)
        and time_label.strip()
        and isinstance(day_notice, str)
        and day_notice.strip()
    ):
        feed_times = _parse_time_minutes(time_label)
        notice_times = _parse_time_minutes(day_notice)
        if feed_times and notice_times:
            tol = int(rules.get("time_tolerance_minutes", 30))
            min_diff = min(
                abs(ft - nt) for ft in feed_times for nt in notice_times
            )
            if min_diff > tol:
                rejections.append(
                    Rejection(
                        layer="cross_ref",
                        rejected_field="feed↔messages:time",
                        reason="time_mismatch",
                        detail=(
                            f"feed.time_label 시각과 messages.day_of_notice 시각 차이 "
                            f"{min_diff}분 > 허용 {tol}분"
                        ),
                        instruction=(
                            f"messages.day_of_notice 안내 시각을 feed.time_label "
                            f"('{time_label}') 기준 ±{tol}분 이내로 맞춰라."
                        ),
                    )
                )

    return rejections


# ---------------------------------------------------------------------------
# Pair 5. review ↔ activity_result
# ---------------------------------------------------------------------------


def _pair_review_activity_result(
    review: Dict[str, Any],
    spec: ContentSpec,
    rules: Dict[str, Any],
) -> List[Rejection]:
    rejections: List[Rejection] = []
    ar = spec.activity_result
    if ar is None:
        return rejections  # recruiting 상태면 이 pair 자체 skip.

    text = review.get("review_text")
    if not isinstance(text, str):
        text = ""

    # 5-a. 노쇼 있었는데 '전원/모두' 표현.
    forbidden = rules.get("forbidden_unanimous_terms") or []
    if ar.no_show_count > 0 and text:
        for term in forbidden:
            if term and term in text:
                rejections.append(
                    Rejection(
                        layer="cross_ref",
                        rejected_field="review↔activity_result:noshow",
                        reason="review_noshow_contradiction",
                        detail=(
                            f"no_show_count={ar.no_show_count} 인데 "
                            f"review_text 에 '{term}' 표현 포함"
                        ),
                        instruction=(
                            f"review_text 에서 '{term}' 같은 총원 긍정 표현을 제거하고, "
                            f"{ar.actual_participants}명만 참여한 상황을 반영하라."
                        ),
                    )
                )

    # 5-b. overall_sentiment 와 review.sentiment 강한 불일치만 reject.
    #      (positive ↔ negative 반대면 reject, neutral 은 어느 쪽과도 허용.)
    review_sentiment = review.get("sentiment")
    if isinstance(review_sentiment, str):
        gt = ar.overall_sentiment
        if (gt == "positive" and review_sentiment == "negative") or (
            gt == "negative" and review_sentiment == "positive"
        ):
            rejections.append(
                Rejection(
                    layer="cross_ref",
                    rejected_field="review↔activity_result:sentiment",
                    reason="sentiment_strong_mismatch",
                    detail=(
                        f"activity_result.overall_sentiment='{gt}' 인데 "
                        f"review.sentiment='{review_sentiment}' (정반대)"
                    ),
                    instruction=(
                        f"review.sentiment 와 rating 을 activity_result.overall_sentiment='{gt}' "
                        "에 맞춰 재작성하라."
                    ),
                )
            )

    return rejections


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def validate_cross_reference(
    spot_id: str,
    *,
    feed: Optional[Dict[str, Any]] = None,
    detail: Optional[Dict[str, Any]] = None,
    plan: Optional[Dict[str, Any]] = None,
    messages: Optional[Dict[str, Any]] = None,
    review: Optional[Dict[str, Any]] = None,
    spec: ContentSpec,
    rules: Optional[Dict[str, Any]] = None,
) -> ValidationResult:
    """5 쌍 cross-reference 를 모두 실행하고 하나의 ValidationResult 로 합성.

    Args:
        spot_id: 로깅/디버깅 용.
        feed/detail/plan/messages/review: 각 content type payload. None 이면 관련 pair 는 skip.
        spec: ContentSpec (builder 출력 — ground truth).
        rules: ``load_cross_reference_rules`` 결과. None 이면 기본 yaml 로드.

    Returns:
        ValidationResult(layer="cross_ref", ...). rejection 의 rejected_field 는
        ``"<pair>:<sub_field>"`` 형태 (재시도 루프가 regenerate 대상을 식별).
    """
    if rules is None:
        rules = load_cross_reference_rules()

    rejections: List[Rejection] = []
    skipped_pairs: List[str] = []
    executed_pairs: List[str] = []

    # Pair 1
    if feed and detail:
        executed_pairs.append("feed↔detail")
        rejections.extend(_pair_feed_detail(feed, detail, spec, rules))
    else:
        skipped_pairs.append("feed↔detail")

    # Pair 2
    if detail and plan:
        executed_pairs.append("detail↔plan")
        rejections.extend(_pair_detail_plan(detail, plan, spec, rules))
    else:
        skipped_pairs.append("detail↔plan")

    # Pair 3
    if detail and review:
        executed_pairs.append("detail↔review")
        rejections.extend(_pair_detail_review(detail, review, spec, rules))
    else:
        skipped_pairs.append("detail↔review")

    # Pair 4
    if feed and messages:
        executed_pairs.append("feed↔messages")
        rejections.extend(_pair_feed_messages(feed, messages, spec, rules))
    else:
        skipped_pairs.append("feed↔messages")

    # Pair 5
    if review is not None:
        executed_pairs.append("review↔activity_result")
        rejections.extend(_pair_review_activity_result(review, spec, rules))
    else:
        skipped_pairs.append("review↔activity_result")

    meta = {
        "spot_id": spot_id,
        "executed_pairs": executed_pairs,
        "skipped_pairs": skipped_pairs,
        "similarity_backend": _SIMILARITY_BACKEND,
        "rule_count": len(rejections),
    }
    return ValidationResult.from_rejections("cross_ref", rejections, meta=meta)


__all__ = [
    "validate_cross_reference",
    "load_cross_reference_rules",
]
