"""실제 카카오 dump 기반 demo — 2 spot × 5 type primary = **10 codex calls**.

Spot 1: 수원 연무동 × 스마트폰 사진 출사 (park venue, 4명, 180분)
Spot 2: 수원 인계동 × 보드게임 모임 (cafe venue, 5명, 150분)

PIPELINE_POI_PATH 환경변수가 ``data/poi/poi_normalized_real.json`` 으로
설정되어야 실제 카카오 POI 사용. 미설정 시 fallback fixture (50 합성 POI).

실행:
    SCP_LLM_MODE=live PIPELINE_POI_PATH=data/poi/poi_normalized_real.json \\
    py scripts/demo_real_pipeline_2_spots.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline.poi.repository import get_default_repository
from pipeline.poi.routing import assign_poi_roles
from pipeline.spec.draft_plan import build_plan_steps_draft
from pipeline.spec.draft_preparation import build_preparation_draft
from pipeline.spec.draft_price import build_price_breakdown_draft
from pipeline.spec.models import (
    ActivityConstraints,
    Budget,
    ContentSpec,
    FeeBreakdownSpec,
    HostPersona,
    Participants,
    Schedule,
)
from pipeline.spec.poi_config import load_distance_rules, load_skill_to_tag


SPOT_RECIPES = [
    {
        "spot_id": "REAL_S0001",
        "region_emd": "연무동",
        "skill_topic": "스마트폰 사진",
        "category": "스마트폰 사진",
        "venue_type": "park",
        "expected_count": 4,
        "duration_minutes": 180,
        "schedule_date": "2026-05-23",
        "start_time": "14:00",
        "fee_per_partner": 15000,
        "material_cost": 0,
        "equipment_rental": 0,
        "teach_mode": "small_group",
        "host_skill_level": 4,
        "taste_facets": ["역광 피하기", "골목 사진"],
        "recent_obsession": "필름 카메라로 동네 다시 보는 거에 푹 빠져 있어요",
        "curiosity_hooks": ["수동 노출", "암실 인화"],
    },
    {
        "spot_id": "REAL_S0002",
        "region_emd": "인계동",
        "skill_topic": "보드게임",
        "category": "보드게임",
        "venue_type": "cafe",
        "expected_count": 5,
        "duration_minutes": 150,
        "schedule_date": "2026-05-25",
        "start_time": "19:00",
        "fee_per_partner": 12000,
        "material_cost": 0,
        "equipment_rental": 0,
        "teach_mode": "small_group",
        "host_skill_level": 4,
        "taste_facets": ["전략 보드", "한국식 변형 룰"],
        "recent_obsession": "요즘 캐스케이디아 에 푹 빠져 있어요",
        "curiosity_hooks": ["18XX 경제 게임", "트릭테이킹"],
    },
]


def build_real_spec(recipe: dict) -> ContentSpec:
    repo = get_default_repository()
    skill_to_tag = load_skill_to_tag(strict_superset=False)
    distance_rules = load_distance_rules()

    routing = assign_poi_roles(
        spot_id=recipe["spot_id"],
        skill_topic=recipe["skill_topic"],
        venue_type=recipe["venue_type"],
        region_emd=recipe["region_emd"],
        teach_mode=recipe["teach_mode"],
        duration_minutes=recipe["duration_minutes"],
        repo=repo,
        skill_to_tag=skill_to_tag,
        distance_rules=distance_rules,
    )

    schedule = Schedule(
        date=recipe["schedule_date"],
        start_time=recipe["start_time"],
        duration_minutes=recipe["duration_minutes"],
    )
    fee_breakdown = FeeBreakdownSpec(
        peer_labor_fee=max(
            0, recipe["fee_per_partner"] - recipe["material_cost"] - recipe["equipment_rental"]
        ),
        material_cost=recipe["material_cost"],
        venue_rental=0,
        equipment_rental=recipe["equipment_rental"],
    )
    plan_steps = build_plan_steps_draft(
        schedule=schedule,
        venue_anchors=routing.venue_anchors,
        skill_topic=recipe["skill_topic"],
        teach_mode=recipe["teach_mode"],
    )
    price_breakdown = build_price_breakdown_draft(
        base_fee=recipe["fee_per_partner"],
        fee_breakdown=fee_breakdown,
        skill_topic=recipe["skill_topic"],
        expected_count=recipe["expected_count"],
    )
    preparation = build_preparation_draft(
        skill_topic=recipe["skill_topic"],
        venue_type=recipe["venue_type"],
    )

    return ContentSpec(
        spot_id=recipe["spot_id"],
        region=recipe["region_emd"],
        category=recipe["category"],
        spot_type="casual_meetup",
        host_persona=HostPersona(
            type="supporter_neutral",
            tone="편안하고 또래스러운",
            communication_style="구체적인 안내, 부담 없는 어투",
        ),
        participants=Participants(
            expected_count=recipe["expected_count"], persona_mix=[]
        ),
        schedule=schedule,
        budget=Budget(
            price_band=2, expected_cost_per_person=recipe["fee_per_partner"]
        ),
        activity_constraints=ActivityConstraints(
            indoor=(recipe["venue_type"] != "park"),
            beginner_friendly=True,
            supporter_required=True,
        ),
        plan_outline=["인사", "활동", "마무리"],
        skill_topic=recipe["skill_topic"],
        host_skill_level=recipe["host_skill_level"],
        teach_mode=recipe["teach_mode"],
        venue_type=recipe["venue_type"],
        fee_breakdown=fee_breakdown,
        latitude=routing.primary_pin.lat if routing.primary_pin else None,
        longitude=routing.primary_pin.lng if routing.primary_pin else None,
        peer_tone_required=True,
        taste_facets=recipe["taste_facets"],
        recent_obsession=recipe["recent_obsession"],
        curiosity_hooks=recipe["curiosity_hooks"],
        venue_anchors=routing.venue_anchors,
        primary_pin=routing.primary_pin,
        plan_steps=plan_steps,
        price_breakdown=price_breakdown,
        preparation=preparation,
        poi_fallback_reason=routing.fallback_reason,
    )


def call_generator(content_type: str, spec: ContentSpec):
    """primary candidate 1개만 직접 codex 호출 (variant 루프 우회)."""
    from pipeline.generators.detail import SpotDetailGenerator
    from pipeline.generators.feed import FeedGenerator
    from pipeline.generators.plan import SpotPlanGenerator
    from pipeline.generators.preparation import SpotPreparationGenerator
    from pipeline.generators.price import SpotPriceGenerator
    from pipeline.generators.base import sample_length_bucket
    from pipeline.llm.codex_client import call_codex

    cls_map = {
        "feed": FeedGenerator,
        "detail": SpotDetailGenerator,
        "plan": SpotPlanGenerator,
        "price": SpotPriceGenerator,
        "preparation": SpotPreparationGenerator,
    }
    gen = cls_map[content_type]()
    length_bucket = sample_length_bucket(spec.spot_id, "primary")
    variables = gen.spec_to_variables(
        spec, variant="primary", length_bucket=length_bucket
    )
    return call_codex(
        template_id=gen.template_id,
        variables=variables,
        schema_path=gen.schema_path,
    )


def main() -> int:
    mode = os.environ.get("SCP_LLM_MODE", "stub")
    poi_path = os.environ.get("PIPELINE_POI_PATH", "<default>")
    print("=" * 78)
    print(f"REAL pipeline demo — mode={mode}, poi_path={poi_path}")
    print("=" * 78)

    out: dict = {"spots": []}
    total_calls = 0
    t_start = time.time()

    for recipe in SPOT_RECIPES:
        spec = build_real_spec(recipe)
        print(f"\n[{spec.spot_id}] region={spec.region} skill={spec.skill_topic}")
        print(
            f"  primary_pin: {spec.primary_pin.name if spec.primary_pin else 'None'} "
            f"({spec.latitude}, {spec.longitude})"
        )
        anchors = [(a.role, a.name, a.primary_category) for a in spec.venue_anchors]
        print(f"  anchors: {anchors}")
        print(f"  fallback: {spec.poi_fallback_reason}")

        spot_results = {
            "spot_id": spec.spot_id,
            "region": spec.region,
            "skill_topic": spec.skill_topic,
            "primary_pin": spec.primary_pin.model_dump() if spec.primary_pin else None,
            "venue_anchors": [a.model_dump() for a in spec.venue_anchors],
            "lat": spec.latitude,
            "lng": spec.longitude,
            "results": {},
        }

        for ct in ("feed", "detail", "plan", "price", "preparation"):
            t0 = time.time()
            try:
                payload = call_generator(ct, spec)
                elapsed = time.time() - t0
                total_calls += 1
                print(f"  [{ct}] OK in {elapsed:.1f}s")
                spot_results["results"][ct] = {"ok": True, "payload": payload}
            except Exception as e:
                elapsed = time.time() - t0
                print(f"  [{ct}] FAIL in {elapsed:.1f}s: {e}")
                spot_results["results"][ct] = {"ok": False, "error": str(e)}

        out["spots"].append(spot_results)

    out["meta"] = {
        "total_codex_calls": total_calls,
        "elapsed_seconds": round(time.time() - t_start, 1),
        "mode": mode,
        "poi_path": poi_path,
    }

    out_file = ROOT / "_workspace" / "demo_real_pipeline_output.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 78)
    print(f"TOTAL codex calls: {total_calls}")
    print(f"Elapsed: {out['meta']['elapsed_seconds']}s")
    print(f"Saved: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
