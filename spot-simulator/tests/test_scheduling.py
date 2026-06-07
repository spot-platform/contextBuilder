from __future__ import annotations

import random

from engine.scheduling import pick_scheduled_tick, pick_spot_duration
from models.agent import AgentState


def _agent(agent_id: str = "A_001", persona_type: str = "night_social") -> AgentState:
    return AgentState(
        agent_id=agent_id,
        persona_type=persona_type,
        home_region_id="emd_gwanggyo",
        active_regions=["emd_gwanggyo"],
        interest_categories=["teach"],
        host_score=0.8,
        join_score=0.6,
        fatigue=0.1,
        social_need=0.7,
        current_state="idle",
        schedule_weights={},
        budget_level=2,
    )


CONFIG = {
    "peer": {
        "temporal_variance": {
            "schedule_lead_ticks": {
                "offer": [6, 36],
                "request_matched": [4, 30],
            },
            "duration_ticks": {
                "1:1": [1, 2],
                "small_group": [2, 4],
                "workshop": [3, 6],
            },
            "persona_rhythm_offsets": {
                "default": [0, 5],
                "night": [6, 14],
            },
        }
    }
}


def test_offer_scheduled_tick_is_future_and_varies_by_rng() -> None:
    host = _agent()
    rng = random.Random(42)
    leads = [
        pick_scheduled_tick(
            current_tick=10,
            host=host,
            skill="핸드드립",
            teach_mode="small_group",
            venue_type="cafe",
            origination_mode="offer",
            rng=rng,
            config=CONFIG,
        )
        - 10
        for _ in range(20)
    ]

    assert min(leads) >= 1
    assert len(set(leads)) > 1


def test_request_matched_schedule_lead_is_not_fixed_to_legacy_8() -> None:
    host = _agent()
    rng = random.Random(7)
    leads = [
        pick_scheduled_tick(
            current_tick=5,
            host=host,
            skill="영어회화",
            teach_mode="1:1",
            venue_type="cafe",
            origination_mode="request_matched",
            rng=rng,
            config=CONFIG,
        )
        - 5
        for _ in range(20)
    ]

    assert len(set(leads)) > 1
    assert any(lead != 8 for lead in leads)


def test_duration_uses_teach_mode_ranges_and_venue_modifier() -> None:
    rng = random.Random(123)
    durations = [
        pick_spot_duration(
            skill="클라이밍",
            teach_mode="workshop",
            venue_type="gym",
            rng=rng,
            config=CONFIG,
        )
        for _ in range(20)
    ]

    assert min(durations) >= 4  # workshop min 3 + gym modifier 1
    assert max(durations) <= 7  # workshop max 6 + gym modifier 1
    assert len(set(durations)) > 1
