"""Build paper/evaluation ContentSpec fixtures for feed RAGAS experiments.

The generated specs are intentionally self-consistent: when skill, schedule,
venue type, or price changes, all derived contexts (plan outline, plan steps,
price/preparation, and POI role wording) are updated together. This avoids
penalizing contextBuilder with contradictory evaluation contexts.

Usage:
    uv run --extra dev python scripts/build_eval_specs.py --n 3
    uv run --extra dev python scripts/build_eval_specs.py --n 100 --out data/eval/feed_specs_100.json
"""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from demo_poi_anchored_generate import build_demo_spec  # type: ignore  # noqa: E402
from pipeline.spec.draft_preparation import build_preparation_draft  # noqa: E402
from pipeline.spec.draft_price import build_price_breakdown_draft  # noqa: E402


SCENARIOS: list[dict[str, Any]] = [
    {
        "skill": "스마트폰 사진",
        "venue_type": "park",
        "teach_mode": "small_group",
        "taste_facets": ["골목 사진", "역광 피하기"],
        "obsession": "동네 골목을 사진으로 다시 보는 데 빠져 있어요",
        "hooks": ["처음 시작하기", "동네에서 같이 해보기"],
        "plan_activity": "스마트폰 사진 구도와 빛 방향을 같이 연습",
        "partner_brings": ["충전된 스마트폰", "편한 신발"],
    },
    {
        "skill": "필름 사진",
        "venue_type": "park",
        "teach_mode": "small_group",
        "taste_facets": ["필름 색감", "성곽길 구도"],
        "obsession": "필름 느낌으로 오래된 길을 찍는 걸 좋아해요",
        "hooks": ["필름 색감 보기", "느린 촬영 리듬"],
        "plan_activity": "성곽길을 걸으며 필름 사진 구도와 색감을 연습",
        "partner_brings": ["필름 카메라 또는 스마트폰", "편한 신발"],
    },
    {
        "skill": "카페 드로잉",
        "venue_type": "cafe",
        "teach_mode": "small_group",
        "taste_facets": ["창가 스케치", "작은 소품"],
        "obsession": "카페 창가에서 작은 장면을 그리는 데 꽂혀 있어요",
        "hooks": ["가벼운 선 연습", "소품 관찰"],
        "plan_activity": "카페 창가에서 작은 소품과 장면을 스케치",
        "partner_brings": ["펜", "작은 스케치북"],
    },
    {
        "skill": "러닝 입문",
        "venue_type": "park",
        "teach_mode": "small_group",
        "taste_facets": ["가벼운 페이스", "초보 루트"],
        "obsession": "무리하지 않고 같이 뛰는 루트를 찾고 있어요",
        "hooks": ["천천히 뛰기", "숨 고르기"],
        "plan_activity": "공원 루트에서 초보 페이스로 짧게 뛰고 걷기",
        "partner_brings": ["운동화", "물"],
    },
    {
        "skill": "홈베이킹",
        "venue_type": "studio",
        "teach_mode": "workshop",
        "taste_facets": ["버터 향", "초보 반죽"],
        "obsession": "간단한 쿠키 반죽을 같이 해보는 걸 좋아해요",
        "hooks": ["반죽 감 잡기", "굽기 타이밍"],
        "plan_activity": "스튜디오에서 초보용 쿠키 반죽과 굽기 흐름을 연습",
        "partner_brings": ["앞치마", "담아갈 작은 통"],
    },
]

TIMES = [
    ("2026-05-23", "10:00"),
    ("2026-05-23", "14:00"),
    ("2026-05-24", "16:00"),
    ("2026-05-30", "19:00"),
    ("2026-05-31", "13:00"),
]

PARTICIPANT_COUNTS = [3, 4, 5, 6]
PRICES = [8000, 12000, 15000, 18000, 22000]


def _hhmm_add(start_time: str, minutes: int) -> str:
    dt = datetime.strptime(start_time, "%H:%M") + timedelta(minutes=minutes)
    return dt.strftime("%H:%M")


def _apply_plan_steps(spec, scenario: dict[str, Any]) -> None:
    anchors = list(spec.venue_anchors)
    meetup = next((a for a in anchors if a.role == "meetup"), None)
    main = next((a for a in anchors if a.role == "main"), None) or spec.primary_pin
    wrapup = next((a for a in anchors if a.role == "wrapup"), None) or meetup

    start = spec.schedule.start_time
    main_time = _hhmm_add(start, 20)
    wrap_time = _hhmm_add(start, max(60, spec.schedule.duration_minutes - 30))

    # Reuse the existing PlanStep model through validation to avoid importing
    # extra internals. The shape matches ContentSpec.plan_steps.
    step_payloads = [
        {
            "time": start,
            "place": meetup.model_dump() if meetup else None,
            "activity": "가볍게 인사하고 오늘 흐름 소개",
            "intent": f"처음 모이는 자리라 {scenario['skill']} 흐름을 부담 없이 맞춰요",
        },
        {
            "time": main_time,
            "place": main.model_dump() if main else None,
            "activity": scenario["plan_activity"],
            "intent": f"{scenario['skill']}을 실제 장소 맥락 안에서 바로 해볼 수 있어요",
        },
        {
            "time": wrap_time,
            "place": wrapup.model_dump() if wrapup else None,
            "activity": "결과물과 후기 한 줄씩 나누기",
            "intent": "마지막에 서로 본 포인트를 나누면 다음 모임으로 이어지기 쉬워요",
        },
    ]
    spec.plan_steps = [type(spec.plan_steps[0]).model_validate(s) for s in step_payloads]


def build_specs(n: int) -> list[dict]:
    base = build_demo_spec()
    cases = []

    for i in range(n):
        spec = deepcopy(base)
        scenario = SCENARIOS[i % len(SCENARIOS)]
        date, start_time = TIMES[i % len(TIMES)]
        price = PRICES[i % len(PRICES)]

        spec.spot_id = f"EVAL_S{i + 1:04d}"
        spec.category = scenario["skill"]
        spec.skill_topic = scenario["skill"]
        spec.venue_type = scenario["venue_type"]
        spec.teach_mode = scenario["teach_mode"]
        spec.taste_facets = list(scenario["taste_facets"])
        spec.recent_obsession = scenario["obsession"]
        spec.curiosity_hooks = list(scenario["hooks"])

        spec.schedule.date = date
        spec.schedule.start_time = start_time
        spec.participants.expected_count = PARTICIPANT_COUNTS[i % len(PARTICIPANT_COUNTS)]
        spec.budget.expected_cost_per_person = price
        spec.budget.price_band = min(5, max(1, 1 + price // 7000))
        spec.activity_constraints.indoor = scenario["venue_type"] in {"cafe", "studio", "home"}

        spec.plan_outline = [
            "가볍게 모여 인사",
            scenario["plan_activity"],
            "마무리하면서 결과물/후기 공유",
        ]

        if spec.fee_breakdown:
            # Keep the fee context coherent with the exposed price.
            spec.fee_breakdown.peer_labor_fee = max(0, price - spec.fee_breakdown.passthrough_total)

        spec.price_breakdown = build_price_breakdown_draft(
            base_fee=price,
            fee_breakdown=spec.fee_breakdown,
            skill_topic=scenario["skill"],
            expected_count=spec.participants.expected_count,
        )
        spec.preparation = build_preparation_draft(
            skill_topic=scenario["skill"],
            venue_type=scenario["venue_type"],
        )
        for item in scenario["partner_brings"]:
            if item not in spec.preparation.partner_brings:
                spec.preparation.partner_brings.append(item)

        _apply_plan_steps(spec, scenario)
        cases.append(spec.model_dump())

    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=3, help="number of ContentSpec cases")
    parser.add_argument("--out", type=Path, help="output JSON path")
    args = parser.parse_args()

    out = args.out or Path(f"data/eval/feed_specs_{args.n}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    cases = build_specs(args.n)
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} specs -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
