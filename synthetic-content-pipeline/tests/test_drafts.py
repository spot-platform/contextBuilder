"""Phase 3c — plan/price/preparation deterministic drafts 단위 테스트."""
from __future__ import annotations

import pytest

from pipeline.spec.draft_plan import build_plan_steps_draft
from pipeline.spec.draft_preparation import build_preparation_draft
from pipeline.spec.draft_price import build_price_breakdown_draft
from pipeline.spec.models import (
    FeeBreakdownSpec,
    ResolvedPlace,
    Schedule,
)


def _rp(role, place_id=1, name="Place", category="cafe"):
    return ResolvedPlace(
        place_id=place_id, name=name, primary_category=category,
        role=role, lat=37.287, lng=127.020, address="x",
    )


# ───────── plan ───────────────────────────────────────────────────────


def test_plan_draft_empty_anchors_returns_empty():
    schedule = Schedule(date="2026-05-18", start_time="14:00", duration_minutes=120)
    steps = build_plan_steps_draft(
        schedule=schedule, venue_anchors=[], skill_topic="기타", teach_mode="small_group",
    )
    assert steps == []


def test_plan_draft_meetup_then_main():
    schedule = Schedule(date="2026-05-18", start_time="14:00", duration_minutes=120)
    anchors = [
        _rp("meetup", 1, "카페1", "cafe"),
        _rp("main", 2, "카페2", "cafe"),
    ]
    steps = build_plan_steps_draft(
        schedule=schedule, venue_anchors=anchors, skill_topic="기타", teach_mode="small_group",
    )
    assert len(steps) >= 2
    assert steps[0].time == "14:00"
    assert steps[0].place.role == "meetup"
    # main step 은 +20분
    assert steps[1].time == "14:20"
    assert steps[1].place.role == "main"


def test_plan_draft_intent_meets_length_constraint():
    schedule = Schedule(date="2026-05-18", start_time="14:00", duration_minutes=180)
    anchors = [
        _rp("meetup", 1, "카페1"),
        _rp("main", 2, "공원1", "park"),
        _rp("wrapup", 3, "카페2"),
    ]
    steps = build_plan_steps_draft(
        schedule=schedule, venue_anchors=anchors, skill_topic="러닝", teach_mode="small_group",
    )
    for s in steps:
        assert 5 <= len(s.intent) <= 120, f"intent length out of bounds: {s.intent}"


def test_plan_draft_workshop_secondary_at_60_percent():
    schedule = Schedule(date="2026-05-18", start_time="14:00", duration_minutes=240)
    anchors = [
        _rp("meetup", 1),
        _rp("main", 2),
        _rp("secondary", 3),
        _rp("wrapup", 4),
    ]
    steps = build_plan_steps_draft(
        schedule=schedule, venue_anchors=anchors, skill_topic="도예 기초", teach_mode="workshop",
    )
    sec_steps = [s for s in steps if s.place and s.place.role == "secondary"]
    assert len(sec_steps) == 1
    # 240분 * 0.6 = 144분 → 14:00 + 144 = 16:24
    assert sec_steps[0].time == "16:24"


def test_plan_draft_steps_time_sorted():
    schedule = Schedule(date="2026-05-18", start_time="14:00", duration_minutes=180)
    anchors = [
        _rp("wrapup", 4, "카페3"),  # role 순서가 아니어도
        _rp("meetup", 1),
        _rp("main", 2),
    ]
    steps = build_plan_steps_draft(
        schedule=schedule, venue_anchors=anchors, skill_topic="기타", teach_mode="small_group",
    )
    times = [s.time for s in steps]
    assert times == sorted(times), f"plan steps not time-sorted: {times}"


# ───────── price ──────────────────────────────────────────────────────


def test_price_draft_minimal():
    pb = build_price_breakdown_draft(
        base_fee=15000,
        fee_breakdown=None,
        skill_topic="보드게임",
        expected_count=4,
    )
    assert pb.base_fee == 15000
    # 최소 1 included
    assert len(pb.included_items) >= 1
    assert pb.refund_policy is not None
    assert pb.refund_policy.cutoff_hours == 72


def test_price_draft_with_material_and_equipment():
    fb = FeeBreakdownSpec(
        peer_labor_fee=5000, material_cost=3500, venue_rental=0, equipment_rental=2000,
    )
    pb = build_price_breakdown_draft(
        base_fee=15000, fee_breakdown=fb, skill_topic="기타", expected_count=4,
    )
    # included_items: 재료 + 가이드 (venue 없음)
    names = [i.name for i in pb.included_items]
    assert any("가이드" in n for n in names)
    # equipment_rental → addon (4명이라 funding)
    assert len(pb.optional_addons) == 1
    assert pb.optional_addons[0].mechanism == "funding"
    assert pb.optional_addons[0].name == "기타 대여"


def test_price_draft_low_capacity_uses_fixed():
    """capacity < 4 면 equipment 가 fixed."""
    fb = FeeBreakdownSpec(
        peer_labor_fee=5000, material_cost=0, venue_rental=0, equipment_rental=2000,
    )
    pb = build_price_breakdown_draft(
        base_fee=10000, fee_breakdown=fb, skill_topic="기타", expected_count=2,
    )
    assert pb.optional_addons[0].mechanism == "fixed"


def test_price_draft_venue_rental_in_included():
    fb = FeeBreakdownSpec(
        peer_labor_fee=0, material_cost=0, venue_rental=4000, equipment_rental=0,
    )
    pb = build_price_breakdown_draft(
        base_fee=8000, fee_breakdown=fb, skill_topic="피아노 기초", expected_count=3,
    )
    names = [i.name for i in pb.included_items]
    assert any("공간" in n or "사용" in n for n in names)


# ───────── preparation ────────────────────────────────────────────────


def test_preparation_draft_running_in_park():
    prep = build_preparation_draft(skill_topic="러닝", venue_type="park")
    assert "러닝화" in prep.partner_brings or "운동화" in str(prep.partner_brings)
    assert prep.weather_contingency is not None  # park venue


def test_preparation_draft_indoor_skill_no_weather():
    prep = build_preparation_draft(skill_topic="보드게임", venue_type="cafe")
    assert prep.weather_contingency is None


def test_preparation_draft_unknown_skill_default():
    prep = build_preparation_draft(skill_topic="외계어 강좌", venue_type="cafe")
    assert prep.host_provides == ["진행 자료"]
    assert prep.partner_brings == ["편하게 오시면 됩니다"]


def test_preparation_draft_safety_for_cooking():
    prep = build_preparation_draft(skill_topic="홈쿡", venue_type="home")
    assert any("알레르기" in n for n in prep.safety_notes)


def test_preparation_draft_safety_for_climbing():
    prep = build_preparation_draft(skill_topic="볼더링", venue_type="gym")
    assert any("부상" in n for n in prep.safety_notes)


def test_preparation_draft_none_skill():
    prep = build_preparation_draft(skill_topic=None, venue_type=None)
    assert prep.host_provides == []
    assert prep.partner_brings == []
    assert prep.weather_contingency is None
