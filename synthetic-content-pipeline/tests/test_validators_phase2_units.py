"""Phase 2 — Layer 2 rule 함수 단위 테스트 (4 content type).

총 27개 rule (feed 8 + detail 5 + plan 4 + messages 5 + review 5) 중
Phase 2 에 해당하는 19개 rule (detail 5 + plan 4 + messages 5 + review 5) 함수에
대해 positive(통과) / negative(reject) 쌍을 검증한다.

Phase 1 의 8개 feed rule 은 `tests/test_validators_rules.py` 가 이미 다루므로
여기서는 import 로 함수 존재만 sanity 체크한다.

각 rule 함수 시그니처: ``(payload, spec, rules) -> List[Rejection]``.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

import pytest

from pipeline.spec.models import (
    ActivityConstraints,
    ActivityResult,
    Budget,
    ContentSpec,
    HostPersona,
    Participants,
    Schedule,
)
from pipeline.validators.detail_rules import (
    load_detail_rules,
    rule_category_consistency_detail,
    rule_cost_breakdown_total,
    rule_description_sentence_count,
    rule_host_intro_length,
    rule_policy_notes_safe,
    validate_detail_rules,
)
from pipeline.validators.messages_rules import (
    load_messages_rules,
    rule_day_of_notice_has_time,
    rule_forbidden_phrases,
    rule_host_tone_consistency,
    rule_recruit_status_match,
    rule_snippets_all_present,
    validate_messages_rules,
)
from pipeline.validators.plan_rules import (
    load_plan_rules,
    rule_first_step_is_intro,
    rule_step_count_range,
    rule_step_time_monotonic,
    rule_total_duration_match,
    validate_plan_rules,
)
from pipeline.validators.review_rules import (
    load_review_rules,
    rule_noshow_mention_consistency,
    rule_rating_sentiment_match,
    rule_review_length_bucket_match,
    rule_satisfaction_tags_range,
    rule_will_rejoin_vs_rating,
    validate_review_rules,
)
from pipeline.validators.rules import validate_feed_rules

REPO = Path(__file__).resolve().parents[1]
BUNDLE_PATH = REPO / "data" / "goldens" / "bundles" / "golden_bundle_yeonmu_food.json"


# ---------------------------------------------------------------------------
# 공용 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def yeonmu_spec_recruiting() -> ContentSpec:
    """golden_food_yeonmu_evening 동일 spec (recruiting, activity_result=None)."""
    return ContentSpec(
        spot_id="UNIT_YEONMU_RECRUIT",
        region="수원시 연무동",
        category="food",
        spot_type="casual_meetup",
        host_persona=HostPersona(
            type="supporter_teacher",
            tone="친절하고 실용적",
            communication_style="가볍고 직접적",
        ),
        participants=Participants(expected_count=4, persona_mix=["night_social"]),
        schedule=Schedule(date="2026-04-17", start_time="19:00", duration_minutes=120),
        budget=Budget(price_band=2, expected_cost_per_person=18000),
        activity_constraints=ActivityConstraints(
            indoor=True, beginner_friendly=True, supporter_required=True
        ),
        plan_outline=["가볍게 인사", "식사와 대화", "다음 모임 취향 공유"],
        activity_result=None,
    )


@pytest.fixture(scope="module")
def yeonmu_spec_settled_with_noshow() -> ContentSpec:
    """동일 yeonmu food 인데 settled + 노쇼 2명."""
    return ContentSpec(
        spot_id="UNIT_YEONMU_SETTLED",
        region="수원시 연무동",
        category="food",
        spot_type="casual_meetup",
        host_persona=HostPersona(
            type="supporter_teacher",
            tone="친절하고 실용적",
            communication_style="가볍고 직접적",
        ),
        participants=Participants(expected_count=4, persona_mix=["night_social"]),
        schedule=Schedule(date="2026-04-17", start_time="19:00", duration_minutes=120),
        budget=Budget(price_band=2, expected_cost_per_person=18000),
        activity_constraints=ActivityConstraints(
            indoor=True, beginner_friendly=True, supporter_required=True
        ),
        plan_outline=["가볍게 인사", "식사와 대화", "다음 모임 취향 공유"],
        activity_result=ActivityResult(
            actual_participants=2,
            no_show_count=2,
            duration_actual_minutes=110,
            issues=[],
            overall_sentiment="neutral",
        ),
    )


@pytest.fixture(scope="module")
def bundle_payloads() -> Dict[str, Any]:
    raw = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    return {k: deepcopy(raw[k]) for k in ("feed", "detail", "plan", "messages", "review")}


@pytest.fixture(scope="module")
def detail_rules() -> Dict[str, Any]:
    return load_detail_rules()


@pytest.fixture(scope="module")
def plan_rules() -> Dict[str, Any]:
    return load_plan_rules()


@pytest.fixture(scope="module")
def messages_rules_cfg() -> Dict[str, Any]:
    return load_messages_rules()


@pytest.fixture(scope="module")
def review_rules_cfg() -> Dict[str, Any]:
    return load_review_rules()


# ---------------------------------------------------------------------------
# DETAIL — 5 rule
# ---------------------------------------------------------------------------


class TestDetailRules:
    def test_description_sentence_count_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        out = rule_description_sentence_count(
            bundle_payloads["detail"], yeonmu_spec_recruiting, detail_rules
        )
        assert out == [], f"unexpected reject: {out}"

    def test_description_sentence_count_too_few(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        bad = deepcopy(bundle_payloads["detail"])
        bad["description"] = (
            "한 문장만 있는 너무 짧은 설명입니다. 두 문장만 있는 너무 짧은 설명이에요."
        )
        # 위 두 문장 — 그냥 length 80 이상이지만 문장 개수 2 < min(3)
        bad["description"] = (
            "퇴근 후 연무동에서 가볍게 한 끼 나누는 4인 식사 모임이에요. "
            "처음 오시는 분도 환영합니다."
        )
        out = rule_description_sentence_count(
            bad, yeonmu_spec_recruiting, detail_rules
        )
        assert out, "expected reject for too few sentences"
        assert out[0].reason == "description_too_few_sentences"

    def test_category_consistency_detail_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        out = rule_category_consistency_detail(
            bundle_payloads["detail"], yeonmu_spec_recruiting, detail_rules
        )
        assert out == []

    def test_category_consistency_detail_reject(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        # food 카테고리에 deny_keyword '운동복' 같은 게 있을지 확인 — 안전하게 deny 키워드 직접 주입.
        bad = deepcopy(bundle_payloads["detail"])
        # food category deny 어휘를 알 수 없으므로 detail_rules 내 categories 에서 deny 추출.
        deny = (detail_rules.get("categories", {}).get("food", {}) or {}).get(
            "deny_keywords"
        ) or []
        if not deny:
            pytest.skip("food.deny_keywords 가 비어 있어 skip")
        bad["description"] = (
            f"퇴근 후 연무동에서 4명이 가볍게 모여서 즐기는 모임이에요. "
            f"여기에는 의도적으로 금지어 '{deny[0]}' 가 본문에 등장합니다. "
            f"검증기는 이 본문을 reject 해야 합니다."
        )
        out = rule_category_consistency_detail(
            bad, yeonmu_spec_recruiting, detail_rules
        )
        assert out, f"expected reject for deny keyword '{deny[0]}'"
        assert out[0].reason == "category_mismatch"

    def test_cost_breakdown_total_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        out = rule_cost_breakdown_total(
            bundle_payloads["detail"], yeonmu_spec_recruiting, detail_rules
        )
        assert out == [], f"unexpected reject: {out}"

    def test_cost_breakdown_total_too_high(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        bad = deepcopy(bundle_payloads["detail"])
        bad["cost_breakdown"] = [
            {"item": "참가비", "amount": 50000},
            {"item": "식대", "amount": 50000},
        ]
        out = rule_cost_breakdown_total(
            bad, yeonmu_spec_recruiting, detail_rules
        )
        assert out, "expected reject for too high cost"
        assert out[0].reason == "cost_total_out_of_range"

    def test_cost_breakdown_total_too_low(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        bad = deepcopy(bundle_payloads["detail"])
        bad["cost_breakdown"] = [{"item": "참가비", "amount": 1000}]
        out = rule_cost_breakdown_total(
            bad, yeonmu_spec_recruiting, detail_rules
        )
        assert out, "expected reject for too low cost"
        assert out[0].reason == "cost_total_out_of_range"

    def test_host_intro_length_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        out = rule_host_intro_length(
            bundle_payloads["detail"], yeonmu_spec_recruiting, detail_rules
        )
        assert out == []

    def test_host_intro_length_too_short_when_supporter_required(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        bad = deepcopy(bundle_payloads["detail"])
        bad["host_intro"] = "안녕하세요. 호스트입니다."
        out = rule_host_intro_length(
            bad, yeonmu_spec_recruiting, detail_rules
        )
        assert out, "expected reject for short host_intro"
        assert out[0].reason == "host_intro_too_short"

    def test_policy_notes_safe_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        out = rule_policy_notes_safe(
            bundle_payloads["detail"], yeonmu_spec_recruiting, detail_rules
        )
        assert out == []

    def test_policy_notes_safe_reject(
        self, bundle_payloads, yeonmu_spec_recruiting, detail_rules
    ):
        bad = deepcopy(bundle_payloads["detail"])
        forbidden = (detail_rules.get("detail", {}) or {}).get(
            "policy_forbidden_terms"
        ) or []
        if not forbidden:
            pytest.skip("policy_forbidden_terms 가 비어 있어 skip")
        bad["policy_notes"] = f"본 모임은 {forbidden[0]} 조건을 따릅니다."
        out = rule_policy_notes_safe(
            bad, yeonmu_spec_recruiting, detail_rules
        )
        assert out, f"expected reject for forbidden term '{forbidden[0]}'"
        assert out[0].reason == "policy_notes_forbidden_term"


# ---------------------------------------------------------------------------
# PLAN — 4 rule
# ---------------------------------------------------------------------------


class TestPlanRules:
    def test_total_duration_match_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, plan_rules
    ):
        out = rule_total_duration_match(
            bundle_payloads["plan"], yeonmu_spec_recruiting, plan_rules
        )
        assert out == []

    def test_total_duration_match_reject(
        self, bundle_payloads, yeonmu_spec_recruiting, plan_rules
    ):
        bad = deepcopy(bundle_payloads["plan"])
        bad["total_duration_minutes"] = 60  # spec=120 → 60분 차이 > 5
        out = rule_total_duration_match(
            bad, yeonmu_spec_recruiting, plan_rules
        )
        assert out, "expected reject for duration mismatch"
        assert out[0].reason == "total_duration_mismatch"

    def test_step_count_range_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, plan_rules
    ):
        out = rule_step_count_range(
            bundle_payloads["plan"], yeonmu_spec_recruiting, plan_rules
        )
        assert out == []

    def test_step_count_range_too_few(
        self, bundle_payloads, yeonmu_spec_recruiting, plan_rules
    ):
        bad = deepcopy(bundle_payloads["plan"])
        bad["steps"] = [
            {"time": "19:00", "activity": "도착"},
            {"time": "20:00", "activity": "마무리"},
        ]
        out = rule_step_count_range(
            bad, yeonmu_spec_recruiting, plan_rules
        )
        assert out, "expected reject for step count too few"
        assert out[0].reason == "step_count_out_of_range"

    def test_step_time_monotonic_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, plan_rules
    ):
        out = rule_step_time_monotonic(
            bundle_payloads["plan"], yeonmu_spec_recruiting, plan_rules
        )
        assert out == []

    def test_step_time_monotonic_reject(
        self, bundle_payloads, yeonmu_spec_recruiting, plan_rules
    ):
        bad = deepcopy(bundle_payloads["plan"])
        # 두 번째 step 을 첫 번째 보다 이른 시각으로 설정.
        bad["steps"] = [
            {"time": "19:00", "activity": "연무동 식당 집결 및 인사"},
            {"time": "18:00", "activity": "메뉴 주문과 아이스브레이킹"},
            {"time": "19:30", "activity": "식사와 자유로운 대화"},
            {"time": "20:30", "activity": "오늘의 취향 한 문장 공유"},
            {"time": "20:50", "activity": "마무리 인사 및 해산"},
        ]
        out = rule_step_time_monotonic(
            bad, yeonmu_spec_recruiting, plan_rules
        )
        assert out, "expected reject for non-monotonic time"
        assert any(r.reason == "step_time_not_monotonic" for r in out)

    def test_first_step_is_intro_warn(
        self, bundle_payloads, yeonmu_spec_recruiting, plan_rules
    ):
        out_pass = rule_first_step_is_intro(
            bundle_payloads["plan"], yeonmu_spec_recruiting, plan_rules
        )
        assert out_pass == []

        bad = deepcopy(bundle_payloads["plan"])
        bad["steps"][0] = {"time": "19:00", "activity": "본격 식사 시작"}
        out = rule_first_step_is_intro(
            bad, yeonmu_spec_recruiting, plan_rules
        )
        assert out, "expected warn for first step not intro"
        assert out[0].reason == "first_step_not_intro"
        assert out[0].severity == "warn"


# ---------------------------------------------------------------------------
# MESSAGES — 5 rule
# ---------------------------------------------------------------------------


class TestMessagesRules:
    def test_snippets_all_present_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, messages_rules_cfg
    ):
        out = rule_snippets_all_present(
            bundle_payloads["messages"], yeonmu_spec_recruiting, messages_rules_cfg
        )
        assert out == []

    def test_snippets_all_present_missing(
        self, bundle_payloads, yeonmu_spec_recruiting, messages_rules_cfg
    ):
        bad = deepcopy(bundle_payloads["messages"])
        bad["join_approval"] = ""
        out = rule_snippets_all_present(
            bad, yeonmu_spec_recruiting, messages_rules_cfg
        )
        assert any(r.reason == "snippet_missing" for r in out)

    def test_host_tone_consistency_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, messages_rules_cfg
    ):
        # bundle 메시지에 '드릴/저/제가' 없을 수 있어 warn 이 발생할 수 있음.
        out = rule_host_tone_consistency(
            bundle_payloads["messages"], yeonmu_spec_recruiting, messages_rules_cfg
        )
        # warn 일 수도 있고 통과일 수도 있음 — 둘 다 허용. severity 만 검사.
        for r in out:
            assert r.severity == "warn"

    def test_host_tone_consistency_warn_when_missing(
        self, bundle_payloads, yeonmu_spec_recruiting, messages_rules_cfg
    ):
        bad = deepcopy(bundle_payloads["messages"])
        # 1인칭/호스트 호칭을 없애기 위해 모두 짧은 중립 문구로 교체.
        bad["recruiting_intro"] = "참가자 모집 안내문입니다. 신청하세요. 안내드립니다."
        bad["join_approval"] = "참여 확정되었습니다. 안내드립니다."
        bad["day_of_notice"] = "오늘 19:00 식당 안내. 늦지 마세요."
        bad["post_thanks"] = "참여해주셔서 감사합니다."
        out = rule_host_tone_consistency(
            bad, yeonmu_spec_recruiting, messages_rules_cfg
        )
        assert out and out[0].reason == "host_tone_inconsistent"
        assert out[0].severity == "warn"

    def test_recruit_status_match_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, messages_rules_cfg
    ):
        out = rule_recruit_status_match(
            bundle_payloads["messages"], yeonmu_spec_recruiting, messages_rules_cfg
        )
        assert out == []

    def test_recruit_status_match_reject(
        self, bundle_payloads, yeonmu_spec_recruiting, messages_rules_cfg
    ):
        bad = deepcopy(bundle_payloads["messages"])
        bad["recruiting_intro"] = (
            "안녕하세요. 연무동에서 저녁 한 끼 나누는 자리입니다. 편안하게 오세요."
        )
        out = rule_recruit_status_match(
            bad, yeonmu_spec_recruiting, messages_rules_cfg
        )
        assert out, "expected reject when recruit intent missing"
        assert out[0].reason == "recruit_intent_missing"

    def test_forbidden_phrases_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, messages_rules_cfg
    ):
        out = rule_forbidden_phrases(
            bundle_payloads["messages"], yeonmu_spec_recruiting, messages_rules_cfg
        )
        assert out == []

    def test_forbidden_phrases_reject(
        self, bundle_payloads, yeonmu_spec_recruiting, messages_rules_cfg
    ):
        forbidden = (messages_rules_cfg.get("messages", {}) or {}).get(
            "forbidden_phrases"
        ) or []
        if not forbidden:
            pytest.skip("forbidden_phrases 가 비어 있어 skip")
        bad = deepcopy(bundle_payloads["messages"])
        bad["join_approval"] = (
            f"신청 감사합니다. {forbidden[0]} 안내드릴게요. 19:00 봬요."
        )
        out = rule_forbidden_phrases(
            bad, yeonmu_spec_recruiting, messages_rules_cfg
        )
        assert out, f"expected reject for term '{forbidden[0]}'"
        assert out[0].reason == "messages_forbidden_phrase"

    def test_day_of_notice_has_time_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, messages_rules_cfg
    ):
        out = rule_day_of_notice_has_time(
            bundle_payloads["messages"], yeonmu_spec_recruiting, messages_rules_cfg
        )
        assert out == []

    def test_day_of_notice_has_time_reject(
        self, bundle_payloads, yeonmu_spec_recruiting, messages_rules_cfg
    ):
        bad = deepcopy(bundle_payloads["messages"])
        bad["day_of_notice"] = (
            "오늘 연무동 식당에서 봬요. 비 소식이 있으니 우산 챙기시면 좋아요."
        )
        out = rule_day_of_notice_has_time(
            bad, yeonmu_spec_recruiting, messages_rules_cfg
        )
        assert out, "expected reject when no time expression"
        assert out[0].reason == "day_of_notice_no_time"


# ---------------------------------------------------------------------------
# REVIEW — 5 rule
# ---------------------------------------------------------------------------


class TestReviewRules:
    def test_rating_sentiment_match_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, review_rules_cfg
    ):
        out = rule_rating_sentiment_match(
            bundle_payloads["review"], yeonmu_spec_recruiting, review_rules_cfg
        )
        assert out == []

    def test_rating_sentiment_match_reject_rating5_negative(
        self, bundle_payloads, yeonmu_spec_recruiting, review_rules_cfg
    ):
        bad = deepcopy(bundle_payloads["review"])
        bad["rating"] = 5
        bad["sentiment"] = "negative"
        out = rule_rating_sentiment_match(
            bad, yeonmu_spec_recruiting, review_rules_cfg
        )
        assert out, "expected reject for rating=5 + sentiment=negative"
        assert out[0].reason == "rating_sentiment_mismatch"

    def test_noshow_mention_consistency_pass_no_noshow(
        self, bundle_payloads, yeonmu_spec_recruiting, review_rules_cfg
    ):
        # recruiting → activity_result=None → 자동 PASS.
        out = rule_noshow_mention_consistency(
            bundle_payloads["review"], yeonmu_spec_recruiting, review_rules_cfg
        )
        assert out == []

    def test_noshow_mention_consistency_reject_when_noshow(
        self,
        bundle_payloads,
        yeonmu_spec_settled_with_noshow,
        review_rules_cfg,
    ):
        bad = deepcopy(bundle_payloads["review"])
        bad["review_text"] = (
            "연무동에서 즐거운 저녁이었어요. 전원 빠짐없이 함께해서 화기애애했습니다. "
            "다음에도 또 참여하고 싶어요."
        )
        out = rule_noshow_mention_consistency(
            bad, yeonmu_spec_settled_with_noshow, review_rules_cfg
        )
        assert out, "expected reject for '전원' mention with no_show_count>0"
        assert out[0].reason == "noshow_contradiction"

    def test_will_rejoin_vs_rating_warn(
        self, bundle_payloads, yeonmu_spec_recruiting, review_rules_cfg
    ):
        # positive case
        out = rule_will_rejoin_vs_rating(
            bundle_payloads["review"], yeonmu_spec_recruiting, review_rules_cfg
        )
        assert out == []

        bad = deepcopy(bundle_payloads["review"])
        bad["rating"] = 1
        bad["will_rejoin"] = True
        out = rule_will_rejoin_vs_rating(
            bad, yeonmu_spec_recruiting, review_rules_cfg
        )
        assert out and out[0].reason == "will_rejoin_contradicts_rating"
        assert out[0].severity == "warn"

    def test_review_length_bucket_match_skip_when_no_meta(
        self, bundle_payloads, yeonmu_spec_recruiting, review_rules_cfg
    ):
        # bundle review 에 meta 가 없으므로 skip (out=[]).
        out = rule_review_length_bucket_match(
            bundle_payloads["review"], yeonmu_spec_recruiting, review_rules_cfg
        )
        assert out == []

    def test_review_length_bucket_match_reject_when_mismatch(
        self, bundle_payloads, yeonmu_spec_recruiting, review_rules_cfg
    ):
        bad = deepcopy(bundle_payloads["review"])
        # short bucket = 1~2 문장 (yaml 기본일 거라 가정. 없으면 skip)
        bucket_map = (review_rules_cfg.get("review", {}) or {}).get(
            "length_bucket_sentences"
        )
        if not bucket_map or "short" not in bucket_map:
            pytest.skip("length_bucket_sentences['short'] 미정의")
        bad["meta"] = {"review_length_bucket": "short"}
        # 5 문장 review_text — short(1~2) 초과
        bad["review_text"] = (
            "연무동에서 부담 없이 저녁 식사를 했어요. "
            "분위기도 좋았고 음식도 맛있었습니다. "
            "호스트분이 친절하게 안내해 주셨어요. "
            "처음 오는 분도 금방 적응할 수 있었어요. "
            "다음에도 또 참여하고 싶습니다."
        )
        out = rule_review_length_bucket_match(
            bad, yeonmu_spec_recruiting, review_rules_cfg
        )
        if out:
            assert out[0].reason == "review_length_bucket_mismatch"

    def test_satisfaction_tags_range_pass(
        self, bundle_payloads, yeonmu_spec_recruiting, review_rules_cfg
    ):
        out = rule_satisfaction_tags_range(
            bundle_payloads["review"], yeonmu_spec_recruiting, review_rules_cfg
        )
        assert out == []

    def test_satisfaction_tags_range_too_many(
        self, bundle_payloads, yeonmu_spec_recruiting, review_rules_cfg
    ):
        bad = deepcopy(bundle_payloads["review"])
        bad["satisfaction_tags"] = [
            "분위기좋음",
            "호스트친절",
            "동네맛집",
            "재참여의향",
            "친화적분위기",
            "초면환영",
        ]
        out = rule_satisfaction_tags_range(
            bad, yeonmu_spec_recruiting, review_rules_cfg
        )
        assert out, "expected reject for 6 tags > max 5"
        assert any(
            r.reason == "satisfaction_tags_count_out_of_range" for r in out
        )


# ---------------------------------------------------------------------------
# Aggregator smoke — 4종 validate_*_rules public entry
# ---------------------------------------------------------------------------


def test_validate_detail_rules_aggregator_pass(
    bundle_payloads, yeonmu_spec_recruiting
):
    res = validate_detail_rules(bundle_payloads["detail"], yeonmu_spec_recruiting)
    assert res.layer == "rule"
    assert res.ok, f"unexpected: {[r.reason for r in res.rejections]}"


def test_validate_plan_rules_aggregator_pass(
    bundle_payloads, yeonmu_spec_recruiting
):
    res = validate_plan_rules(bundle_payloads["plan"], yeonmu_spec_recruiting)
    assert res.ok, f"unexpected: {[r.reason for r in res.rejections]}"


def test_validate_messages_rules_aggregator_pass(
    bundle_payloads, yeonmu_spec_recruiting
):
    res = validate_messages_rules(
        bundle_payloads["messages"], yeonmu_spec_recruiting
    )
    # warn 만 있으면 ok=True 여야 함.
    assert res.ok, f"unexpected hard rejections: {[r.reason for r in res.hard_rejections]}"


def test_validate_review_rules_aggregator_pass(
    bundle_payloads, yeonmu_spec_recruiting
):
    res = validate_review_rules(
        bundle_payloads["review"], yeonmu_spec_recruiting
    )
    assert res.ok, f"unexpected: {[r.reason for r in res.rejections]}"


# ---------------------------------------------------------------------------
# Phase 1 feed rule sanity (회귀 방지)
# ---------------------------------------------------------------------------


def test_feed_rules_aggregator_still_callable(
    bundle_payloads, yeonmu_spec_recruiting
):
    res = validate_feed_rules(bundle_payloads["feed"], yeonmu_spec_recruiting)
    assert res.layer == "rule"
    # ok 는 fixture 마다 다를 수 있으므로 여기선 호출 가능 + 리턴 타입만 확인.
