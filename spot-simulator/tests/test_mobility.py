from __future__ import annotations

import random

from engine.mobility import process_pending_returns, schedule_returns_for_completed_spot
from models.agent import AgentState
from models.spot import Spot


def _agent(agent_id: str, persona_type: str, home_region_id: str = "emd_gwanggyo") -> AgentState:
    return AgentState(
        agent_id=agent_id,
        persona_type=persona_type,
        home_region_id=home_region_id,
        active_regions=[home_region_id],
        interest_categories=["teach"],
        host_score=0.5,
        join_score=0.5,
        fatigue=0.1,
        social_need=0.5,
        current_state="checked_in",
        schedule_weights={},
        budget_level=2,
    )


def test_completed_spot_returns_are_staggered_and_emit_leave_then_return() -> None:
    host = _agent("A_host", "night_social")
    p1 = _agent("A_p1", "homebody")
    p2 = _agent("A_p2", "social")
    spot = Spot(
        spot_id="S_0001",
        host_agent_id=host.agent_id,
        region_id="emd_gwanggyo",
        category="teach",
        capacity=3,
        min_participants=2,
        scheduled_tick=10,
        created_at_tick=1,
        duration=3,
        completed_at_tick=13,
    )
    spot.checked_in.update({p1.agent_id, p2.agent_id})

    config = {
        "peer": {
            "temporal_variance": {
                "return_home": {
                    "linger_ticks_by_persona": {
                        "default": [0, 3],
                        "homebody": [0, 1],
                        "social": [1, 4],
                        "night_social": [2, 6],
                    },
                    "travel_ticks": {"same_region": [0, 1]},
                }
            }
        }
    }
    pending = schedule_returns_for_completed_spot(
        spot=spot,
        agents_by_id={a.agent_id: a for a in [host, p1, p2]},
        tick=13,
        rng=random.Random(42),
        config=config,
    )

    assert {p.persona_id for p in pending} == {"A_host", "A_p1", "A_p2"}
    assert len({p.return_home_tick for p in pending}) > 1

    emitted = []
    for tick in range(13, 25):
        emitted.extend(process_pending_returns(pending, tick))

    event_types = [event.event_type for event in emitted]
    assert "PERSONA_LEAVE_SPOT" in event_types
    assert "PERSONA_RETURN_HOME" in event_types
    assert pending == []
