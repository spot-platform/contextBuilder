"""Phase 2 — ContentSpec POI 확장 모델 단위 테스트.

핵심 회귀 보호:
1. 신규 5 모델 (ResolvedPlace, PlanStep, PriceBreakdown, AddOn, Preparation) 인스턴스화.
2. ContentSpec 에 신규 5 필드가 default 값으로 깨지지 않게 추가됐는지.
3. 기존 ContentSpec 인스턴스 (POI 필드 미지정) 가 여전히 valid 한지.
4. BaseGenerator.spec_to_variables 가 신규 5 변수를 모두 채우는지.
5. COMMON_VARIABLE_KEYS frozenset 에 5 키가 모두 추가됐는지.
"""
from __future__ import annotations

import pytest

from pipeline.generators.base import COMMON_VARIABLE_KEYS, BaseGenerator
from pipeline.spec.models import (
    AddOn,
    ActivityConstraints,
    Budget,
    ContentSpec,
    HostPersona,
    IncludedItem,
    Participants,
    Preparation,
    PriceBreakdown,
    PlanStep,
    RefundPolicy,
    ResolvedPlace,
    Schedule,
)


def _make_resolved_place(role="meetup", place_id=100001):
    return ResolvedPlace(
        place_id=place_id,
        name="연무 커피그라인더",
        primary_category="cafe",
        role=role,
        lat=37.2873,
        lng=127.0205,
        address="수원시 팔달구 연무동 12-3",
        road_address="수원시 팔달구 연무로 12",
        confidence=0.95,
    )


def _make_minimal_spec(**overrides) -> ContentSpec:
    base = dict(
        spot_id="S_TEST",
        region="연무동",
        category="만남",
        host_persona=HostPersona(
            type="supporter_neutral",
            tone="편안하고 친근한",
            communication_style="짧고 명료한",
        ),
        participants=Participants(expected_count=4),
        schedule=Schedule(date="2026-05-18", start_time="14:00", duration_minutes=120),
        budget=Budget(price_band=2, expected_cost_per_person=15000),
        activity_constraints=ActivityConstraints(),
        plan_outline=["인사", "활동", "마무리"],
    )
    base.update(overrides)
    return ContentSpec(**base)


# ───────── 신규 5 모델 인스턴스화 ──────────────────────────────────────


def test_resolved_place_basic():
    p = _make_resolved_place()
    assert p.role == "meetup"
    assert p.confidence == 0.95
    assert p.lat == 37.2873


def test_resolved_place_role_literal_enforced():
    with pytest.raises(Exception):  # pydantic ValidationError
        ResolvedPlace(
            place_id=1, name="x", primary_category="cafe", role="invalid_role",
            lat=0.0, lng=0.0, address="x",
        )


def test_resolved_place_lat_lng_bounds():
    with pytest.raises(Exception):
        ResolvedPlace(
            place_id=1, name="x", primary_category="cafe", role="meetup",
            lat=999.0, lng=0.0, address="x",
        )


def test_plan_step_intent_required():
    with pytest.raises(Exception):
        PlanStep(time="14:00", activity="x")  # intent missing


def test_plan_step_intent_too_short():
    with pytest.raises(Exception):
        PlanStep(time="14:00", activity="만남", intent="ㅇ")  # < 5자


def test_plan_step_intent_too_long():
    with pytest.raises(Exception):
        PlanStep(time="14:00", activity="만남", intent="x" * 200)  # > 120자


def test_plan_step_with_resolved_place():
    step = PlanStep(
        time="14:00",
        place=_make_resolved_place(),
        activity="가볍게 인사 나누기",
        intent="처음이라 카페에서 5분 정도 인사로 풀어요",
    )
    assert step.place is not None
    assert step.place.role == "meetup"


def test_plan_step_without_place():
    """이동 없는 step (예: 워밍업) 은 place=None 허용."""
    step = PlanStep(
        time="14:00",
        activity="워밍업 스트레칭",
        intent="다리 풀고 시작하면 페이스 안 무너져요",
    )
    assert step.place is None


def test_addon_mechanism_literal():
    a = AddOn(name="필름카메라 대여", price=15000, mechanism="funding")
    assert a.mechanism == "funding"
    with pytest.raises(Exception):
        AddOn(name="x", price=1000, mechanism="invalid_mechanism")


def test_price_breakdown_with_includes_and_addons():
    pb = PriceBreakdown(
        base_fee=35000,
        included_items=[
            IncludedItem(name="필름 1롤", value="코닥 골드 200"),
            IncludedItem(name="가이드", value="3시간"),
        ],
        optional_addons=[
            AddOn(name="필름카메라 대여", price=15000, mechanism="funding"),
        ],
        refund_policy=RefundPolicy(cutoff_hours=72, full_refund_until="활동 4일 전까지"),
    )
    assert pb.base_fee == 35000
    assert len(pb.included_items) == 2
    assert pb.optional_addons[0].mechanism == "funding"


def test_preparation_full():
    prep = Preparation(
        host_provides=["필름카메라 5대", "코닥 골드 200"],
        partner_brings=["굽 낮은 운동화", "물 한 병"],
        weather_contingency="강수 30% 이상 시 24일로 연기",
        safety_notes=["성곽길 미끄럼 주의"],
    )
    assert prep.host_provides[0] == "필름카메라 5대"
    assert prep.weather_contingency is not None


def test_preparation_minimal():
    """모든 필드 default 가능."""
    prep = Preparation()
    assert prep.host_provides == []
    assert prep.partner_brings == []
    assert prep.weather_contingency is None


# ───────── ContentSpec 회귀 안전성 ─────────────────────────────────────


def test_content_spec_minimal_no_poi_fields():
    """기존 ContentSpec 인스턴스 (POI 필드 미지정) 가 여전히 valid."""
    spec = _make_minimal_spec()
    assert spec.venue_anchors == []
    assert spec.primary_pin is None
    assert spec.plan_steps == []
    assert spec.price_breakdown is None
    assert spec.preparation is None
    assert spec.poi_fallback_reason is None


def test_content_spec_with_poi_fields():
    spec = _make_minimal_spec(
        venue_anchors=[
            _make_resolved_place(role="meetup", place_id=1),
            _make_resolved_place(role="main", place_id=2),
        ],
        primary_pin=_make_resolved_place(role="main", place_id=2),
        plan_steps=[
            PlanStep(
                time="14:00",
                place=_make_resolved_place(role="meetup", place_id=1),
                activity="만남",
                intent="첫인사 카페에서 5분",
            ),
        ],
        price_breakdown=PriceBreakdown(base_fee=15000),
        preparation=Preparation(host_provides=["커피"]),
        poi_fallback_reason=None,
    )
    assert len(spec.venue_anchors) == 2
    assert spec.primary_pin.place_id == 2
    assert spec.price_breakdown.base_fee == 15000


def test_content_spec_serialization_roundtrip():
    spec = _make_minimal_spec(
        venue_anchors=[_make_resolved_place()],
        primary_pin=_make_resolved_place(),
    )
    dumped = spec.model_dump()
    assert "venue_anchors" in dumped
    assert "primary_pin" in dumped
    assert "plan_steps" in dumped
    assert "price_breakdown" in dumped
    assert "preparation" in dumped
    assert "poi_fallback_reason" in dumped

    # round-trip
    spec2 = ContentSpec(**dumped)
    assert spec2.primary_pin is not None
    assert spec2.primary_pin.name == "연무 커피그라인더"


# ───────── COMMON_VARIABLE_KEYS / spec_to_variables ─────────────────────


def test_common_variable_keys_includes_poi_keys():
    for key in [
        "venue_anchors",
        "primary_pin",
        "plan_steps",
        "price_breakdown",
        "preparation",
    ]:
        assert key in COMMON_VARIABLE_KEYS, f"missing key: {key}"


def test_spec_to_variables_includes_poi_keys_empty():
    """POI 필드가 default 인 spec 에서도 변수가 모두 채워져야 함."""
    spec = _make_minimal_spec()
    gen = BaseGenerator()
    vars_ = gen.spec_to_variables(spec, variant="primary", length_bucket="medium")
    assert vars_["venue_anchors"] == []
    assert vars_["primary_pin"] is None
    assert vars_["plan_steps"] == []
    assert vars_["price_breakdown"] is None
    assert vars_["preparation"] is None


def test_spec_to_variables_includes_poi_keys_filled():
    spec = _make_minimal_spec(
        venue_anchors=[_make_resolved_place()],
        primary_pin=_make_resolved_place(role="main"),
        plan_steps=[
            PlanStep(
                time="14:00",
                activity="만남",
                intent="첫인사 카페에서 5분",
            )
        ],
        price_breakdown=PriceBreakdown(base_fee=15000),
        preparation=Preparation(host_provides=["커피"]),
    )
    gen = BaseGenerator()
    vars_ = gen.spec_to_variables(spec, variant="primary", length_bucket="medium")
    assert isinstance(vars_["venue_anchors"], list)
    assert vars_["venue_anchors"][0]["role"] == "meetup"
    assert vars_["primary_pin"]["role"] == "main"
    assert vars_["plan_steps"][0]["activity"] == "만남"
    assert vars_["price_breakdown"]["base_fee"] == 15000
    assert vars_["preparation"]["host_provides"] == ["커피"]


def test_spec_to_variables_no_missing_keys():
    """COMMON_VARIABLE_KEYS - vars_.keys() 가 빈 set 이어야 한다."""
    spec = _make_minimal_spec()
    gen = BaseGenerator()
    vars_ = gen.spec_to_variables(spec, variant="primary", length_bucket="short")
    missing = COMMON_VARIABLE_KEYS - vars_.keys()
    assert missing == set(), f"missing variable keys: {sorted(missing)}"
