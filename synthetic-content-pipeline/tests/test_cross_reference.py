"""Phase 2 — Layer 3 cross-reference (5 pair) positive/negative 테스트.

각 pair 별:
    1. feed ↔ detail            (price/region/category/supporter)
    2. detail ↔ plan            (활동 키워드 / 자료 정합)
    3. detail ↔ review          (활동 종류 모순)
    4. feed ↔ messages          (모집 상태 / 시각)
    5. review ↔ activity_result (노쇼/sentiment)

bundle 픽스처:
    - golden_bundle_yeonmu_food.json         — 5쌍 모두 PASS positive
    - golden_bundle_inconsistent.json        — 4쌍 reject (food vs drawing 모순)
    - golden_bundle_noshow_contradiction.json — review↔activity_result reject
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

import pytest

from pipeline.spec.models import ContentSpec
from pipeline.validators.cross_reference import (
    load_cross_reference_rules,
    validate_cross_reference,
)
from pipeline.validators.dispatch import run_cross_reference

REPO = Path(__file__).resolve().parents[1]
BUNDLES = REPO / "data" / "goldens" / "bundles"
SPECS = REPO / "data" / "goldens" / "specs"


def _load_bundle(name: str) -> Dict[str, Any]:
    return json.loads((BUNDLES / name).read_text(encoding="utf-8"))


def _load_spec(name: str) -> ContentSpec:
    return ContentSpec.model_validate(
        json.loads((SPECS / name).read_text(encoding="utf-8"))
    )


def _payloads(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        k: deepcopy(raw[k])
        for k in ("feed", "detail", "plan", "messages", "review")
        if k in raw
    }


@pytest.fixture(scope="module")
def cr_rules() -> Dict[str, Any]:
    return load_cross_reference_rules()


@pytest.fixture(scope="module")
def yeonmu_spec() -> ContentSpec:
    return _load_spec("golden_food_yeonmu_evening.json")


# ---------------------------------------------------------------------------
# Positive — 정상 번들
# ---------------------------------------------------------------------------


def test_positive_yeonmu_bundle_all_pairs_pass(yeonmu_spec, cr_rules):
    raw = _load_bundle("golden_bundle_yeonmu_food.json")
    bundle = _payloads(raw)
    res = run_cross_reference(bundle, yeonmu_spec)
    assert res.ok, (
        f"positive bundle should pass cross-ref but got: "
        f"{[(r.rejected_field, r.reason) for r in res.rejections]}"
    )
    assert set(res.meta["executed_pairs"]) == {
        "feed↔detail",
        "detail↔plan",
        "detail↔review",
        "feed↔messages",
        "review↔activity_result",
    }
    assert res.meta["skipped_pairs"] == []


# ---------------------------------------------------------------------------
# Negative — golden_bundle_inconsistent.json (4 pair reject)
# ---------------------------------------------------------------------------


def test_negative_inconsistent_bundle_detects_multiple_pairs(
    yeonmu_spec, cr_rules
):
    raw = _load_bundle("golden_bundle_inconsistent.json")
    bundle = _payloads(raw)
    res = run_cross_reference(bundle, yeonmu_spec)
    assert not res.ok, "inconsistent bundle should reject"
    fields = {r.rejected_field for r in res.hard_rejections}
    # 핵심 4개 reject 가 모두 잡혀야 한다.
    expected = {
        "feed↔detail:category",
        "detail:cost_breakdown",
        "feed↔detail:price",
        "detail↔review:activity_kind",
        "feed↔messages:time",
    }
    missing = expected - fields
    assert not missing, (
        f"missing expected rejections: {missing}; got fields={fields}"
    )


# ---------------------------------------------------------------------------
# Pair 별 네거티브 — 미세한 변형으로 단일 pair 만 reject 되는지 확인
# ---------------------------------------------------------------------------


def test_pair_feed_detail_category_mismatch(yeonmu_spec, cr_rules):
    raw = _load_bundle("golden_bundle_yeonmu_food.json")
    bundle = _payloads(raw)
    # detail 본문에서 food 키워드를 모두 제거하고 culture 어휘로 치환.
    bundle["detail"]["title"] = "연무동 드로잉 클래스 4명 모집"
    bundle["detail"]["description"] = (
        "연무동 작업실에서 4명이 모여 그림을 그리는 드로잉 클래스예요. "
        "처음 그려보는 분도 호스트가 곁에서 도와드리니 편하게 신청하세요. "
        "마지막에 작품을 함께 감상하는 시간도 가져요."
    )
    bundle["detail"]["activity_purpose"] = (
        "연무동에서 드로잉을 통해 마음을 정리하고 작품을 공유하는 것"
    )
    bundle["detail"]["progress_style"] = (
        "도착 후 도구 안내, 시범 스케치, 자유 드로잉, 작품 감상 순으로 진행해요. "
        "호스트가 처음 그리는 분도 편안하게 도와드려요."
    )
    res = validate_cross_reference(
        spot_id=yeonmu_spec.spot_id,
        feed=bundle["feed"],
        detail=bundle["detail"],
        spec=yeonmu_spec,
        rules=cr_rules,
    )
    rejs = [r.rejected_field for r in res.hard_rejections]
    assert "feed↔detail:category" in rejs, f"expected category mismatch, got {rejs}"


def test_pair_feed_messages_time_mismatch(yeonmu_spec, cr_rules):
    raw = _load_bundle("golden_bundle_yeonmu_food.json")
    bundle = _payloads(raw)
    # messages.day_of_notice 의 시각을 오후 3시로 변경 (feed 19:00 vs 15:00 → 240분 차이)
    bundle["messages"]["day_of_notice"] = (
        "오늘 오후 3시에 연무동 작업실에서 봬요. 늦지 않게 와 주시고 편한 차림으로 오세요."
    )
    res = validate_cross_reference(
        spot_id=yeonmu_spec.spot_id,
        feed=bundle["feed"],
        messages=bundle["messages"],
        spec=yeonmu_spec,
        rules=cr_rules,
    )
    rejs = [r.rejected_field for r in res.hard_rejections]
    assert "feed↔messages:time" in rejs, f"expected time mismatch, got {rejs}"


def test_pair_detail_plan_activity_mismatch(yeonmu_spec, cr_rules):
    raw = _load_bundle("golden_bundle_yeonmu_food.json")
    bundle = _payloads(raw)
    # plan steps 에 food 키워드 아예 제거 + 운동 키워드.
    bundle["plan"]["steps"] = [
        {"time": "19:00", "activity": "공원 집결 및 인사"},
        {"time": "19:10", "activity": "준비 운동과 스트레칭"},
        {"time": "19:30", "activity": "러닝 코스 시작"},
        {"time": "20:30", "activity": "쿨다운 산책"},
        {"time": "20:50", "activity": "마무리 인사 및 해산"},
    ]
    # detail 은 food 키워드 풍부한 그대로.
    res = validate_cross_reference(
        spot_id=yeonmu_spec.spot_id,
        detail=bundle["detail"],
        plan=bundle["plan"],
        spec=yeonmu_spec,
        rules=cr_rules,
    )
    fields = [r.rejected_field for r in res.hard_rejections]
    assert "detail↔plan:activity" in fields, f"expected detail↔plan mismatch, got {fields}"


def test_pair_detail_review_activity_kind_mismatch(yeonmu_spec, cr_rules):
    raw = _load_bundle("golden_bundle_yeonmu_food.json")
    bundle = _payloads(raw)
    # review_text 에 culture 키워드만 등장 (food 어휘 제거).
    bundle["review"]["review_text"] = (
        "연무동 작업실에서 차분히 그림을 그릴 수 있어서 좋았어요. "
        "전시 작품을 감상하는 시간도 인상 깊었습니다."
    )
    res = validate_cross_reference(
        spot_id=yeonmu_spec.spot_id,
        detail=bundle["detail"],
        review=bundle["review"],
        spec=yeonmu_spec,
        rules=cr_rules,
    )
    fields = [r.rejected_field for r in res.hard_rejections]
    assert "detail↔review:activity_kind" in fields, f"expected review activity kind mismatch, got {fields}"


def test_pair_review_activity_result_noshow_contradiction():
    raw = _load_bundle("golden_bundle_noshow_contradiction.json")
    spec = ContentSpec.model_validate(raw["spec_inline"])
    bundle = _payloads(raw)
    res = run_cross_reference(bundle, spec)
    fields = [r.rejected_field for r in res.hard_rejections]
    assert "review↔activity_result:noshow" in fields, (
        f"expected noshow contradiction, got {fields}"
    )


def test_pair_review_activity_result_sentiment_strong_mismatch():
    raw = _load_bundle("golden_bundle_noshow_contradiction.json")
    spec_dict = deepcopy(raw["spec_inline"])
    # overall_sentiment 를 negative 로 강제.
    spec_dict["activity_result"]["overall_sentiment"] = "negative"
    spec_dict["activity_result"]["no_show_count"] = 0
    spec = ContentSpec.model_validate(spec_dict)
    bundle = _payloads(raw)
    # review.sentiment 는 positive (raw 와 동일) → strong mismatch.
    res = run_cross_reference(bundle, spec)
    fields = [r.rejected_field for r in res.hard_rejections]
    assert "review↔activity_result:sentiment" in fields, (
        f"expected sentiment strong mismatch, got {fields}"
    )


# ---------------------------------------------------------------------------
# Skip behavior — 일부 content type 만 있을 때 관련 pair skip
# ---------------------------------------------------------------------------


def test_partial_bundle_skips_unrelated_pairs(yeonmu_spec, cr_rules):
    raw = _load_bundle("golden_bundle_yeonmu_food.json")
    bundle = _payloads(raw)
    # plan/messages/review 빠진 부분 번들.
    partial = {"feed": bundle["feed"], "detail": bundle["detail"]}
    res = run_cross_reference(partial, yeonmu_spec)
    # feed↔detail 만 실행, 나머지 4 쌍 skip 되어야 한다.
    assert res.ok, f"feed↔detail should pass: {res.rejections}"
    executed = set(res.meta["executed_pairs"])
    assert "feed↔detail" in executed
    skipped = set(res.meta["skipped_pairs"])
    assert {"detail↔plan", "feed↔messages", "review↔activity_result"} <= skipped
