"""Phase 5 — POI cross-reference pair 단위 테스트."""
from __future__ import annotations

from typing import Any, Dict

import pytest

from pipeline.spec.models import (
    ActivityConstraints,
    Budget,
    ContentSpec,
    HostPersona,
    Participants,
    ResolvedPlace,
    Schedule,
)
from pipeline.validators.cross_reference import validate_cross_reference


def _spec(anchors=None, primary_pin=None, region="연무동", expected_cost=15000):
    return ContentSpec(
        spot_id="S_TEST",
        region=region,
        category="스마트폰 사진",
        host_persona=HostPersona(
            type="supporter_neutral", tone="편안한", communication_style="짧고 명료한",
        ),
        participants=Participants(expected_count=4),
        schedule=Schedule(date="2026-05-23", start_time="14:00", duration_minutes=180),
        budget=Budget(price_band=2, expected_cost_per_person=expected_cost),
        activity_constraints=ActivityConstraints(),
        plan_outline=["인사", "활동", "마무리"],
        venue_anchors=anchors or [],
        primary_pin=primary_pin,
    )


def _rp(place_id, name, role="main", category="cafe"):
    return ResolvedPlace(
        place_id=place_id, name=name, primary_category=category, role=role,
        lat=37.287, lng=127.020,
        address=f"수원시 팔달구 연무동 {place_id}", road_address=None,
    )


# ───────── detail ↔ venue_anchors ──────────────────────────────────────


def test_detail_anchor_match_passes():
    a = _rp(1, "팔달산 산책로", role="main")
    spec = _spec(anchors=[a], primary_pin=a)
    detail = {
        "title": "x", "description": "팔달산 산책로에서 사진 찍어요. 4명이 함께해요. 충분한 디테일.",
        "activity_purpose": "y", "progress_style": "z",
        "materials": [], "target_audience": "x",
        "cost_breakdown": [{"item": "참가비", "amount": 15000}],
        "host_intro": "또래 호스트", "policy_notes": None,
    }
    res = validate_cross_reference("S_TEST", detail=detail, spec=spec)
    assert all(r.rejected_field != "detail:venue_anchor_missing" for r in res.rejections)


def test_detail_anchor_missing_rejects():
    a = _rp(1, "팔달산 산책로", role="main")
    spec = _spec(anchors=[a], primary_pin=a)
    detail = {
        "title": "x", "description": "그냥 일반적인 모임 설명입니다 같이 해봐요. 좋습니다 정말.",
        "activity_purpose": "y", "progress_style": "z",
        "materials": [], "target_audience": "x",
        "cost_breakdown": [{"item": "참가비", "amount": 15000}],
        "host_intro": "또래 호스트", "policy_notes": None,
    }
    res = validate_cross_reference("S_TEST", detail=detail, spec=spec)
    fields = [r.rejected_field for r in res.rejections]
    assert "detail:venue_anchor_missing" in fields


def test_detail_anchor_skipped_when_no_anchors():
    spec = _spec(anchors=[], primary_pin=None)
    detail = {"title": "x", "description": "x" * 80,
              "activity_purpose": "x", "progress_style": "x",
              "materials": [], "target_audience": "x",
              "cost_breakdown": [{"item": "x", "amount": 15000}],
              "host_intro": "x", "policy_notes": None}
    res = validate_cross_reference("S_TEST", detail=detail, spec=spec)
    assert "detail↔venue_anchors" in res.meta["skipped_pairs"]


# ───────── plan ↔ poi ──────────────────────────────────────────────────


def test_plan_place_id_invalid_rejects():
    a = _rp(100, "장소1", role="main")
    spec = _spec(anchors=[a], primary_pin=a)
    plan = {
        "steps": [
            {"time": "14:00", "activity": "시작", "place_id": 100, "intent": "x"*5},
            {"time": "14:30", "activity": "본활동", "place_id": 99999, "intent": "x"*5},
        ],
        "total_duration_minutes": 180,
    }
    res = validate_cross_reference("S_TEST", plan=plan, spec=spec)
    fields = [r.rejected_field for r in res.rejections]
    assert "plan:place_id_invalid" in fields


def test_plan_all_null_warns():
    a = _rp(100, "장소1", role="main")
    spec = _spec(anchors=[a], primary_pin=a)
    plan = {
        "steps": [
            {"time": "14:00", "activity": "시작", "place_id": None, "intent": "x"*5},
            {"time": "14:30", "activity": "본활동", "place_id": None, "intent": "x"*5},
        ],
        "total_duration_minutes": 180,
    }
    res = validate_cross_reference("S_TEST", plan=plan, spec=spec)
    rejections = [r for r in res.rejections if r.rejected_field == "plan:place_id_missing"]
    assert len(rejections) == 1
    assert rejections[0].severity == "warn"


def test_plan_partial_null_no_warn():
    """일부만 null 이면 warn 안 남."""
    a = _rp(100, "장소1", role="main")
    spec = _spec(anchors=[a], primary_pin=a)
    plan = {
        "steps": [
            {"time": "14:00", "activity": "시작", "place_id": 100, "intent": "x"*5},
            {"time": "14:30", "activity": "본활동", "place_id": None, "intent": "x"*5},
        ],
        "total_duration_minutes": 180,
    }
    res = validate_cross_reference("S_TEST", plan=plan, spec=spec)
    fields = [r.rejected_field for r in res.rejections]
    assert "plan:place_id_missing" not in fields
    assert "plan:place_id_invalid" not in fields


# ───────── feed ↔ primary_pin ──────────────────────────────────────────


def test_feed_region_pin_match_passes():
    a = _rp(1, "팔달산 산책로", role="main")
    spec = _spec(anchors=[a], primary_pin=a, region="연무동")
    feed = {"region_label": "수원시 연무동", "title": "x", "summary": "x",
            "tags": [], "price_label": "x", "time_label": "x",
            "status": "recruiting", "supporter_label": "x"}
    res = validate_cross_reference("S_TEST", feed=feed, spec=spec)
    fields = [r.rejected_field for r in res.rejections]
    assert "feed:region_label" not in fields


def test_feed_region_pin_mismatch_warns():
    pin = ResolvedPlace(
        place_id=1, name="x", primary_category="cafe", role="main",
        lat=0.0, lng=0.0, address="강남구 역삼동", road_address=None,
    )
    spec = _spec(anchors=[pin], primary_pin=pin, region="연무동")
    feed = {"region_label": "강남구 역삼", "title": "x", "summary": "x",
            "tags": [], "price_label": "x", "time_label": "x",
            "status": "recruiting", "supporter_label": "x"}
    res = validate_cross_reference("S_TEST", feed=feed, spec=spec)
    fields = [(r.rejected_field, r.severity) for r in res.rejections]
    assert ("feed:region_label", "warn") in fields


# ───────── detail ↔ price ──────────────────────────────────────────────


def test_detail_price_base_fee_in_range_passes():
    a = _rp(1, "x", role="main")
    spec = _spec(anchors=[a], primary_pin=a, expected_cost=15000)
    detail = {"title": "x", "description": "x" * 80, "activity_purpose": "x",
              "progress_style": "x", "materials": [], "target_audience": "x",
              "cost_breakdown": [{"item": "참가비", "amount": 15000}],
              "host_intro": "x", "policy_notes": None}
    price = {"base_fee": 15500, "included_items": [{"name": "x", "value": "x"}],
             "optional_addons": [], "refund_policy": None,
             "summary_line": "x"}
    res = validate_cross_reference("S_TEST", detail=detail, price=price, spec=spec)
    fields = [r.rejected_field for r in res.rejections]
    assert "price:base_fee_mismatch_detail" not in fields


def test_detail_price_base_fee_out_of_range_rejects():
    a = _rp(1, "x", role="main")
    spec = _spec(anchors=[a], primary_pin=a, expected_cost=15000)
    detail = {"title": "x", "description": "x" * 80, "activity_purpose": "x",
              "progress_style": "x", "materials": [], "target_audience": "x",
              "cost_breakdown": [{"item": "참가비", "amount": 15000}],
              "host_intro": "x", "policy_notes": None}
    price = {"base_fee": 50000, "included_items": [{"name": "x", "value": "x"}],
             "optional_addons": [], "refund_policy": None,
             "summary_line": "x"}
    res = validate_cross_reference("S_TEST", detail=detail, price=price, spec=spec)
    fields = [r.rejected_field for r in res.rejections]
    assert "price:base_fee_mismatch_detail" in fields


# ───────── detail ↔ preparation ────────────────────────────────────────


def test_detail_preparation_no_overlap_passes():
    a = _rp(1, "x", role="main")
    spec = _spec(anchors=[a], primary_pin=a)
    detail = {"title": "x", "description": "x" * 80, "activity_purpose": "x",
              "progress_style": "x", "materials": ["편한 신발", "물 한 병"],
              "target_audience": "x",
              "cost_breakdown": [{"item": "참가비", "amount": 15000}],
              "host_intro": "x", "policy_notes": None}
    prep = {"host_provides": ["촬영 가이드"], "partner_brings": ["편한 신발"],
            "weather_contingency": None, "safety_notes": [], "host_tip": None}
    res = validate_cross_reference("S_TEST", detail=detail, preparation=prep, spec=spec)
    fields = [r.rejected_field for r in res.rejections]
    assert "detail:materials_vs_host_provides" not in fields


def test_detail_preparation_overlap_warns():
    a = _rp(1, "x", role="main")
    spec = _spec(anchors=[a], primary_pin=a)
    detail = {"title": "x", "description": "x" * 80, "activity_purpose": "x",
              "progress_style": "x", "materials": ["촬영 가이드"],
              "target_audience": "x",
              "cost_breakdown": [{"item": "참가비", "amount": 15000}],
              "host_intro": "x", "policy_notes": None}
    prep = {"host_provides": ["촬영 가이드"], "partner_brings": [],
            "weather_contingency": None, "safety_notes": [], "host_tip": None}
    res = validate_cross_reference("S_TEST", detail=detail, preparation=prep, spec=spec)
    fields = [(r.rejected_field, r.severity) for r in res.rejections]
    assert ("detail:materials_vs_host_provides", "warn") in fields


# ───────── 회귀: legacy spec (no anchors) 에서 POI pair 모두 skip ─────


def test_legacy_spec_no_anchors_skips_poi_pairs():
    spec = _spec(anchors=[], primary_pin=None)
    feed = {"region_label": "x", "title": "x", "summary": "x",
            "tags": [], "price_label": "x", "time_label": "x",
            "status": "recruiting", "supporter_label": "x"}
    detail = {"title": "x", "description": "x" * 80, "activity_purpose": "x",
              "progress_style": "x", "materials": [], "target_audience": "x",
              "cost_breakdown": [{"item": "x", "amount": 15000}],
              "host_intro": "x", "policy_notes": None}
    plan = {"steps": [
        {"time": "14:00", "activity": "x", "place_id": 99, "intent": "x"*5},
    ], "total_duration_minutes": 180}
    res = validate_cross_reference(
        "S_TEST", feed=feed, detail=detail, plan=plan, spec=spec
    )
    skipped = res.meta["skipped_pairs"]
    assert "detail↔venue_anchors" in skipped
    assert "plan↔poi" in skipped
    assert "feed↔primary_pin" in skipped
