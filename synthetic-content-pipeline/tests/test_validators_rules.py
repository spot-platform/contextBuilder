"""test_validators_rules — 8개 deterministic rule 각각 positive/negative 1쌍."""
from __future__ import annotations

from copy import deepcopy

import pytest

from pipeline.spec.models import (
    ActivityConstraints,
    Budget,
    ContentSpec,
    HostPersona,
    Participants,
    Schedule,
)
from pipeline.validators.rules import (
    load_feed_rules,
    rule_category_consistency,
    rule_host_consistency,
    rule_price_consistency,
    rule_realism_budget,
    rule_realism_duration,
    rule_region_consistency,
    rule_target_consistency,
    rule_time_consistency,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _base_spec(**overrides) -> ContentSpec:
    base = dict(
        spot_id="G_TEST",
        region="수원시 연무동",
        category="food",
        spot_type="casual_meetup",
        host_persona=HostPersona(
            type="supporter_teacher",
            tone="친절하고 실용적",
            communication_style="가볍고 직접적",
        ),
        participants=Participants(expected_count=4, persona_mix=[]),
        schedule=Schedule(date="2026-04-18", start_time="19:00", duration_minutes=120),
        budget=Budget(price_band=2, expected_cost_per_person=18000),
        activity_constraints=ActivityConstraints(
            indoor=True, beginner_friendly=True, supporter_required=True
        ),
        plan_outline=["인사", "식사", "마무리"],
        activity_result=None,
    )
    base.update(overrides)
    return ContentSpec(**base)


def _good_payload():
    return {
        "title": "연무동 저녁 한 끼 같이할 4명 모집",
        "summary": "가볍게 식사하면서 취향을 나누는 소규모 저녁 모임이에요.",
        "tags": ["저녁모임", "소규모", "연무동", "초면환영"],
        "price_label": "1인 1.5~2만원",
        "region_label": "수원시 연무동",
        "time_label": "4/18(금) 19:00",
        "status": "recruiting",
        "supporter_label": "supporter_teacher",
    }


@pytest.fixture(scope="module")
def feed_rules():
    return load_feed_rules()


# ---------------------------------------------------------------------------
# Rule 1. region_consistency
# ---------------------------------------------------------------------------


def test_rule_region_consistency_pass(feed_rules):
    spec = _base_spec()
    out = rule_region_consistency(_good_payload(), spec, feed_rules)
    assert out == []


def test_rule_region_consistency_fail(feed_rules):
    spec = _base_spec(region="수원시 장안동")
    out = rule_region_consistency(_good_payload(), spec, feed_rules)
    assert out and out[0].reason == "region_mismatch"


# ---------------------------------------------------------------------------
# Rule 2. category_consistency
# ---------------------------------------------------------------------------


def test_rule_category_consistency_pass(feed_rules):
    spec = _base_spec()
    out = rule_category_consistency(_good_payload(), spec, feed_rules)
    assert out == []


def test_rule_category_consistency_fail(feed_rules):
    spec = _base_spec(category="food")
    p = _good_payload()
    p["summary"] = "드로잉 클래스 같이할 4명 모집"
    out = rule_category_consistency(p, spec, feed_rules)
    assert out and out[0].reason == "category_mismatch"


# ---------------------------------------------------------------------------
# Rule 3. price_consistency
# ---------------------------------------------------------------------------


def test_rule_price_consistency_pass(feed_rules):
    spec = _base_spec()  # expected=18000 → 허용 9000~45000
    p = _good_payload()  # 1.5~2만원 (15000~20000)
    out = rule_price_consistency(p, spec, feed_rules)
    assert out == []


def test_rule_price_consistency_fail_out_of_range(feed_rules):
    spec = _base_spec()  # 18000 → 허용 9000~45000
    p = _good_payload()
    p["price_label"] = "참가비 120,000원"  # 상한 초과
    out = rule_price_consistency(p, spec, feed_rules)
    assert out and any(r.reason == "price_out_of_range" for r in out)


# ---------------------------------------------------------------------------
# Rule 4. time_consistency
# ---------------------------------------------------------------------------


def test_rule_time_consistency_pass(feed_rules):
    spec = _base_spec()  # 19:00 evening → OK
    out = rule_time_consistency(_good_payload(), spec, feed_rules)
    assert out == []


def test_rule_time_consistency_fail_morning_with_night_word(feed_rules):
    spec = _base_spec(schedule=Schedule(date="2026-04-18", start_time="07:30", duration_minutes=60))
    p = _good_payload()
    p["summary"] = "이른 아침이지만 야식 당기는 분 환영."  # 아침+야식 금지
    out = rule_time_consistency(p, spec, feed_rules)
    assert out and any(r.reason == "time_mismatch_morning" for r in out)


# ---------------------------------------------------------------------------
# Rule 5. target_consistency
# ---------------------------------------------------------------------------


def test_rule_target_consistency_pass(feed_rules):
    spec = _base_spec()  # beginner_friendly=True, 일반 표현
    out = rule_target_consistency(_good_payload(), spec, feed_rules)
    assert out == []


def test_rule_target_consistency_fail_advanced_word(feed_rules):
    spec = _base_spec()
    p = _good_payload()
    p["summary"] = "숙련자 전용 모임이며 초면환영 아닙니다."
    out = rule_target_consistency(p, spec, feed_rules)
    assert out and out[0].reason == "target_mismatch_beginner"


# ---------------------------------------------------------------------------
# Rule 6. host_consistency
# ---------------------------------------------------------------------------


def test_rule_host_consistency_pass(feed_rules):
    spec = _base_spec()
    out = rule_host_consistency(_good_payload(), spec, feed_rules)
    assert out == []


def test_rule_host_consistency_fail_empty_label(feed_rules):
    spec = _base_spec()
    p = _good_payload()
    p["supporter_label"] = ""
    out = rule_host_consistency(p, spec, feed_rules)
    assert out and out[0].reason in ("host_label_empty", "host_label_missing")


# ---------------------------------------------------------------------------
# Rule 7. realism_budget (소규모 모임 1인 단가 상한)
# ---------------------------------------------------------------------------


def test_rule_realism_budget_pass(feed_rules):
    spec = _base_spec()  # expected_count=4, small group
    out = rule_realism_budget(_good_payload(), spec, feed_rules)
    assert out == []


def test_rule_realism_budget_fail_over_max(feed_rules):
    spec = _base_spec()  # small group, max_per_person=50000
    p = _good_payload()
    p["price_label"] = "1인 230,000원"
    out = rule_realism_budget(p, spec, feed_rules)
    assert out and out[0].reason == "realism_budget_too_high"


# ---------------------------------------------------------------------------
# Rule 8. realism_duration
# ---------------------------------------------------------------------------


def test_rule_realism_duration_pass(feed_rules):
    spec = _base_spec()  # 120분 — 포함 단어 없음
    out = rule_realism_duration(_good_payload(), spec, feed_rules)
    assert out == []


def test_rule_realism_duration_fail_eight_hours(feed_rules):
    spec = _base_spec()  # 120분
    p = _good_payload()
    p["summary"] = "하루 종일 함께하는 저녁 모임."
    out = rule_realism_duration(p, spec, feed_rules)
    assert out and out[0].reason == "realism_duration_too_long"
