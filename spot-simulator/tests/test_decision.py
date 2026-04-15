"""Table-driven tests for engine.decision.decide_action (Phase 1).

Each scenario builds a tiny synthetic AgentState / Spot / region fixture and
rolls `decide_action` N times with a fixed `random.Random(123)`. We assert
on the distribution of outcomes (e.g. "CREATE_SPOT appears at least once")
rather than deterministic outputs so the tests survive minor reordering of
RNG draws in the engine.
"""

from __future__ import annotations

import random

import pytest

from engine.decision import decide_action, find_matchable_spots
from models import AgentState, Spot, SpotStatus

N_ROLLS = 200
SEED = 123


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _agent(**over) -> AgentState:
    defaults: dict = dict(
        agent_id="A_actor",
        persona_type="night_social",
        home_region_id="emd_yeonmu",
        active_regions=["emd_yeonmu", "emd_jangan"],
        interest_categories=["food", "cafe"],
        host_score=0.6,
        join_score=0.6,
        fatigue=0.2,
        social_need=0.6,
        current_state="idle",
        # Full coverage: peak weights in evening/night, ~0 in dawn.
        schedule_weights={
            "weekday_dawn": 0.01,
            "weekday_morning": 0.05,
            "weekday_late_morning": 0.10,
            "weekday_lunch": 0.20,
            "weekday_afternoon": 0.25,
            "weekday_evening": 0.95,
            "weekday_night": 0.95,
            "weekend_dawn": 0.01,
            "weekend_morning": 0.10,
            "weekend_late_morning": 0.15,
            "weekend_lunch": 0.25,
            "weekend_afternoon": 0.30,
            "weekend_evening": 0.95,
            "weekend_night": 0.95,
        },
        budget_level=2,
    )
    defaults.update(over)
    return AgentState(**defaults)


def _spot(**over) -> Spot:
    defaults: dict = dict(
        spot_id="S_0001",
        host_agent_id="A_host",  # not the actor
        region_id="emd_yeonmu",  # in actor.active_regions
        category="food",
        capacity=4,
        min_participants=2,
        scheduled_tick=30,
        created_at_tick=10,
        status=SpotStatus.OPEN,
        participants=[],
    )
    defaults.update(over)
    return Spot(**defaults)


REGION_FEATURES: dict = {
    "emd_yeonmu": {
        "region_id": "emd_yeonmu",
        "spot_create_affinity": 0.8,
        "budget_avg_level": 2,
    },
    "emd_jangan": {
        "region_id": "emd_jangan",
        "spot_create_affinity": 0.5,
        "budget_avg_level": 3,
    },
    "emd_sinchon": {
        "region_id": "emd_sinchon",
        "spot_create_affinity": 0.6,
        "budget_avg_level": 1,
    },
}

PERSONA_TEMPLATES: dict = {"night_social": {}}  # adapter unused in Phase 1


def _roll(agent: AgentState, tick: int, open_spots: list[Spot]) -> dict:
    """Roll decide_action N times and return a count breakdown."""
    rng = random.Random(SEED)
    counts: dict[str, int] = {"CREATE_SPOT": 0, "JOIN_SPOT": 0, "NO_ACTION": 0}
    for _ in range(N_ROLLS):
        action, _ = decide_action(
            agent,
            tick,
            open_spots,
            rng=rng,
            region_features=REGION_FEATURES,
            persona_templates=PERSONA_TEMPLATES,
        )
        counts[action] = counts.get(action, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def test_peak_idle_empty_spots_creates_spot():
    """High host_score + idle + peak evening + no spots -> CREATE appears."""
    agent = _agent(
        host_score=0.9,
        social_need=0.8,
        fatigue=0.05,
        current_state="idle",
    )
    # tick=18 -> weekday_evening
    counts = _roll(agent, tick=18, open_spots=[])
    assert counts["CREATE_SPOT"] > 0, counts
    assert counts["JOIN_SPOT"] == 0  # no spots available
    assert counts["CREATE_SPOT"] + counts["NO_ACTION"] == N_ROLLS


def test_dawn_high_fatigue_low_social_dominates_no_action():
    """Dawn + maxed fatigue + no social need -> mostly NO_ACTION."""
    agent = _agent(
        host_score=0.9,
        social_need=0.05,
        fatigue=1.0,
        current_state="idle",
    )
    # tick=3 -> weekday_dawn (weight 0.01 -> almost always NO_ACTION on time gate)
    counts = _roll(agent, tick=3, open_spots=[])
    assert counts["NO_ACTION"] >= int(N_ROLLS * 0.9), counts
    assert counts["CREATE_SPOT"] == 0


def test_matchable_spot_with_high_join_score_joins():
    """Category-matched open spot + high join_score + peak -> JOIN appears."""
    agent = _agent(
        host_score=0.1,
        join_score=0.95,
        social_need=0.8,
        fatigue=0.05,
        current_state="idle",
        interest_categories=["food", "cafe"],
    )
    spot = _spot(category="food", region_id="emd_yeonmu", participants=[])
    counts = _roll(agent, tick=18, open_spots=[spot])
    assert counts["JOIN_SPOT"] > 0, counts


def test_non_matchable_spots_wrong_region_never_join():
    """Spots in a region the agent doesn't cover must never appear as JOIN."""
    agent = _agent(
        join_score=0.95,
        social_need=0.9,
        fatigue=0.05,
        active_regions=["emd_yeonmu"],  # <-- no sinchon
    )
    spot = _spot(
        region_id="emd_sinchon",  # outside active_regions
        category="food",
    )
    counts = _roll(agent, tick=18, open_spots=[spot])
    assert counts["JOIN_SPOT"] == 0, counts


def test_not_idle_state_cannot_create_spot():
    """An agent that is already hosting/joined must not CREATE_SPOT."""
    agent = _agent(
        host_score=1.0,
        social_need=1.0,
        fatigue=0.0,
        current_state="hosting",  # not idle
    )
    counts = _roll(agent, tick=18, open_spots=[])
    assert counts["CREATE_SPOT"] == 0, counts


def test_find_matchable_sorts_category_first_then_participants():
    """find_matchable_spots sort key: (category_match desc, participants desc)."""
    agent = _agent(interest_categories=["food"])
    match_full = _spot(
        spot_id="S_match_full",
        category="food",
        participants=["A_x"],
    )
    match_empty = _spot(
        spot_id="S_match_empty",
        category="food",
        participants=[],
    )
    no_match = _spot(
        spot_id="S_nomatch",
        category="exercise",
        participants=["A_y", "A_z"],
    )
    ranked = find_matchable_spots(
        agent,
        [no_match, match_empty, match_full],
        persona_templates=PERSONA_TEMPLATES,
    )
    # Same-category, more participants ranks first; the no-match tail loses.
    assert ranked[0].spot_id == "S_match_full"
    assert ranked[1].spot_id == "S_match_empty"
    assert ranked[-1].spot_id == "S_nomatch"


def test_full_capacity_spots_are_filtered_out():
    """A spot at capacity must not be returned by find_matchable_spots."""
    agent = _agent()
    full = _spot(capacity=2, participants=["A_x", "A_y"])
    ranked = find_matchable_spots(
        agent, [full], persona_templates=PERSONA_TEMPLATES
    )
    assert ranked == []


def test_host_cannot_join_own_spot():
    """Agent's own hosted spot is filtered out of the join candidates."""
    agent = _agent(agent_id="A_host")
    own = _spot(host_agent_id="A_host")
    ranked = find_matchable_spots(
        agent, [own], persona_templates=PERSONA_TEMPLATES
    )
    assert ranked == []
