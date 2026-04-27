"""POI-anchored pipeline demo — 실제 codex 호출로 1 spot 생성.

사용:
    SCP_LLM_MODE=live py scripts/demo_poi_anchored_generate.py

또는 stub 모드:
    py scripts/demo_poi_anchored_generate.py

수원 연무동 × 스마트폰 사진 출사 spot 을 직접 합성해서 ContentSpec 을
만들고, feed / detail / plan / price / preparation 5 type 의 primary
candidate 를 codex 또는 stub 으로 생성하여 출력한다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# src/ 경로 주입 (pip install -e 안 되어 있을 때)
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


def build_demo_spec() -> ContentSpec:
    """수원 연무동 × 스마트폰 사진 출사 spot ContentSpec."""
    repo = get_default_repository()
    skill_to_tag = load_skill_to_tag(strict_superset=False)
    distance_rules = load_distance_rules()

    routing = assign_poi_roles(
        spot_id="DEMO_S0001",
        skill_topic="스마트폰 사진",
        venue_type="park",
        region_emd="연무동",
        teach_mode="small_group",
        duration_minutes=180,
        repo=repo,
        skill_to_tag=skill_to_tag,
        distance_rules=distance_rules,
    )

    schedule = Schedule(date="2026-05-23", start_time="14:00", duration_minutes=180)

    fee_breakdown = FeeBreakdownSpec(
        peer_labor_fee=12000,
        material_cost=0,
        venue_rental=0,
        equipment_rental=0,
    )

    plan_steps = build_plan_steps_draft(
        schedule=schedule,
        venue_anchors=routing.venue_anchors,
        skill_topic="스마트폰 사진",
        teach_mode="small_group",
    )
    price_breakdown = build_price_breakdown_draft(
        base_fee=15000,
        fee_breakdown=fee_breakdown,
        skill_topic="스마트폰 사진",
        expected_count=4,
    )
    preparation = build_preparation_draft(
        skill_topic="스마트폰 사진",
        venue_type="park",
    )

    spec = ContentSpec(
        spot_id="DEMO_S0001",
        region="연무동",
        category="스마트폰 사진",
        spot_type="casual_meetup",
        host_persona=HostPersona(
            type="supporter_neutral",
            tone="편안하고 또래스러운",
            communication_style="구체적인 안내, 부담 없는 어투",
        ),
        participants=Participants(expected_count=4, persona_mix=[]),
        schedule=schedule,
        budget=Budget(price_band=2, expected_cost_per_person=15000),
        activity_constraints=ActivityConstraints(
            indoor=False, beginner_friendly=True, supporter_required=True
        ),
        plan_outline=[
            "연무동 우체국 앞 집결",
            "동네 골목 + 화성 성곽길 촬영",
            "마무리 카페에서 사진 공유",
        ],
        skill_topic="스마트폰 사진",
        host_skill_level=4,
        teach_mode="small_group",
        venue_type="park",
        fee_breakdown=fee_breakdown,
        latitude=routing.primary_pin.lat if routing.primary_pin else None,
        longitude=routing.primary_pin.lng if routing.primary_pin else None,
        peer_tone_required=True,
        taste_facets=["역광 피하기", "골목 사진"],
        recent_obsession="필름 카메라로 동네 다시 보는 거에 푹 빠져 있어요",
        curiosity_hooks=["수동 노출", "암실 인화"],
        venue_anchors=routing.venue_anchors,
        primary_pin=routing.primary_pin,
        plan_steps=plan_steps,
        price_breakdown=price_breakdown,
        preparation=preparation,
        poi_fallback_reason=routing.fallback_reason,
    )
    return spec


def main() -> int:
    spec = build_demo_spec()

    # 보고용 메타
    print("=" * 78)
    print(f"DEMO spot_id={spec.spot_id} region={spec.region} skill={spec.skill_topic}")
    print(f"  primary_pin: {spec.primary_pin.name if spec.primary_pin else 'None'} "
          f"({spec.latitude}, {spec.longitude})")
    print(f"  venue_anchors ({len(spec.venue_anchors)}):")
    for a in spec.venue_anchors:
        print(f"    [{a.role}] {a.name} ({a.primary_category}, conf={a.confidence:.2f})")
    print(f"  poi_fallback_reason: {spec.poi_fallback_reason}")
    print(f"  LLM mode: {os.environ.get('SCP_LLM_MODE', 'stub')}")
    print("=" * 78)

    # 5 type generator 실행
    from pipeline.generators.detail import SpotDetailGenerator
    from pipeline.generators.feed import FeedGenerator
    from pipeline.generators.plan import SpotPlanGenerator
    from pipeline.generators.preparation import SpotPreparationGenerator
    from pipeline.generators.price import SpotPriceGenerator

    generators = {
        "feed": FeedGenerator,
        "detail": SpotDetailGenerator,
        "plan": SpotPlanGenerator,
        "price": SpotPriceGenerator,
        "preparation": SpotPreparationGenerator,
    }

    results = {}
    for name, cls in generators.items():
        gen = cls()
        cands = gen.generate(spec)
        primary = next((c for c in cands if c.variant == "primary"), cands[0])
        results[name] = {
            "template_id": primary.template_id,
            "stub": primary.meta.get("stub", False),
            "retry_count": primary.meta.get("retry_count", 0),
            "payload": primary.payload,
        }
        print(f"\n--- {name.upper()} ({primary.template_id}, stub={primary.meta.get('stub')}) ---")
        print(json.dumps(primary.payload, ensure_ascii=False, indent=2))

    # 전체 파일로 dump
    out = ROOT / "_workspace" / "demo_poi_anchored_output.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "spec_summary": {
            "spot_id": spec.spot_id,
            "region": spec.region,
            "skill_topic": spec.skill_topic,
            "venue_type": spec.venue_type,
            "primary_pin": spec.primary_pin.model_dump() if spec.primary_pin else None,
            "venue_anchors": [a.model_dump() for a in spec.venue_anchors],
            "latitude": spec.latitude,
            "longitude": spec.longitude,
            "poi_fallback_reason": spec.poi_fallback_reason,
        },
        "results": results,
    }
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved full output → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
