"""Phase 2 lifecycle state-machine tests — plan §3.3 / §3.4.

`engine.lifecycle.process_lifecycle` is the single-pass state machine that
advances spots at most one hop per tick. These tests build tiny spot
fixtures and assert each transition fires (or doesn't) with the exact
event type the plan specifies.

Single-tick transition guarantee: a spot must NOT race through
OPEN→MATCHED→CONFIRMED in the same tick just because `scheduled_tick` is
close. The lifecycle processor only inspects status at entry and fires at
most one transition per invocation.
"""

from __future__ import annotations

import random

import pytest

from engine.lifecycle import (
    CONFIRM_LEAD_TICKS,
    NOSHOW_DISPUTE_THRESHOLD,
    OPEN_TIMEOUT_TICKS,
    process_lifecycle,
)
from models import AgentState, EventLog, Spot, SpotStatus, reset_event_counter


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_agent(agent_id: str = "A_test", **over) -> AgentState:
    defaults: dict = dict(
        agent_id=agent_id,
        persona_type="night_social",
        home_region_id="emd_yeonmu",
        active_regions=["emd_yeonmu"],
        interest_categories=["food"],
        host_score=0.6,
        join_score=0.6,
        fatigue=0.3,
        social_need=0.5,
        current_state="idle",
        schedule_weights={"weekday_evening": 0.9},
        budget_level=2,
    )
    defaults.update(over)
    return AgentState(**defaults)


def _make_spot(**over) -> Spot:
    defaults: dict = dict(
        spot_id="S_life",
        host_agent_id="A_host",
        region_id="emd_yeonmu",
        category="food",
        capacity=4,
        min_participants=2,
        scheduled_tick=20,
        created_at_tick=0,
    )
    defaults.update(over)
    return Spot(**defaults)


def _agents_dict(*agents: AgentState) -> dict[str, AgentState]:
    return {a.agent_id: a for a in agents}


@pytest.fixture(autouse=True)
def _reset_counter():
    reset_event_counter(1)
    yield


@pytest.fixture
def rng() -> random.Random:
    return random.Random(0)


# ---------------------------------------------------------------------------
# 1. OPEN -> CANCELED (timeout)
# ---------------------------------------------------------------------------


def test_open_timeout_transitions_to_canceled(rng: random.Random):
    spot = _make_spot(created_at_tick=0, status=SpotStatus.OPEN)
    events: list[EventLog] = []
    # tick = 49 -> age = 49 > OPEN_TIMEOUT_TICKS (48)
    process_lifecycle(
        [spot], OPEN_TIMEOUT_TICKS + 1, events, _agents_dict(), rng=rng
    )
    assert spot.status == SpotStatus.CANCELED
    assert spot.canceled_at_tick == OPEN_TIMEOUT_TICKS + 1
    assert any(e.event_type == "SPOT_TIMEOUT" for e in events)


def test_open_below_timeout_stays_open(rng: random.Random):
    spot = _make_spot(created_at_tick=0, status=SpotStatus.OPEN)
    events: list[EventLog] = []
    # tick = 48 -> age = 48, not > 48
    process_lifecycle(
        [spot], OPEN_TIMEOUT_TICKS, events, _agents_dict(), rng=rng
    )
    assert spot.status == SpotStatus.OPEN
    assert spot.canceled_at_tick is None
    assert not any(e.event_type == "SPOT_TIMEOUT" for e in events)


# ---------------------------------------------------------------------------
# 2. MATCHED -> CONFIRMED (lead-time gate)
# ---------------------------------------------------------------------------


def test_matched_within_confirm_window_transitions_to_confirmed(rng):
    host = _make_agent("A_host")
    p1 = _make_agent("A_p1")
    p2 = _make_agent("A_p2")
    spot = _make_spot(
        status=SpotStatus.MATCHED,
        scheduled_tick=10,
        created_at_tick=0,
        participants=["A_p1", "A_p2"],
    )
    events: list[EventLog] = []
    # tick = 8 -> scheduled_tick - tick = 2, equal to CONFIRM_LEAD_TICKS
    process_lifecycle(
        [spot],
        spot.scheduled_tick - CONFIRM_LEAD_TICKS,
        events,
        _agents_dict(host, p1, p2),
        rng=rng,
    )
    assert spot.status == SpotStatus.CONFIRMED
    assert spot.confirmed_at_tick == spot.scheduled_tick - CONFIRM_LEAD_TICKS
    assert any(e.event_type == "SPOT_CONFIRMED" for e in events)
    # Lifecycle pins the participants+host into confirmed_spots.
    assert spot.spot_id in host.confirmed_spots
    assert spot.spot_id in p1.confirmed_spots
    assert spot.spot_id in p2.confirmed_spots


def test_matched_outside_confirm_window_stays_matched(rng):
    spot = _make_spot(
        status=SpotStatus.MATCHED,
        scheduled_tick=20,
        created_at_tick=0,
        participants=["A_x", "A_y"],
    )
    events: list[EventLog] = []
    # tick = 10 -> scheduled_tick - tick = 10, > CONFIRM_LEAD_TICKS
    process_lifecycle([spot], 10, events, _agents_dict(), rng=rng)
    assert spot.status == SpotStatus.MATCHED
    assert spot.confirmed_at_tick is None


# ---------------------------------------------------------------------------
# 3. CONFIRMED -> IN_PROGRESS
# ---------------------------------------------------------------------------


def test_confirmed_at_scheduled_tick_transitions_to_in_progress(rng):
    spot = _make_spot(
        status=SpotStatus.CONFIRMED,
        scheduled_tick=15,
        confirmed_at_tick=13,
        participants=["A_p1", "A_p2"],
    )
    events: list[EventLog] = []
    process_lifecycle([spot], 15, events, _agents_dict(), rng=rng)
    assert spot.status == SpotStatus.IN_PROGRESS
    assert spot.started_at_tick == 15
    assert any(e.event_type == "SPOT_STARTED" for e in events)


# ---------------------------------------------------------------------------
# 4. IN_PROGRESS -> COMPLETED (noshow <= threshold)
# ---------------------------------------------------------------------------


def test_in_progress_with_no_noshow_transitions_to_completed(rng):
    host = _make_agent("A_host")
    p1 = _make_agent("A_p1")
    p2 = _make_agent("A_p2")
    spot = _make_spot(
        status=SpotStatus.IN_PROGRESS,
        scheduled_tick=10,
        started_at_tick=10,
        participants=["A_p1", "A_p2"],
        checked_in={"A_host", "A_p1", "A_p2"},
        noshow=set(),
        duration=2,
    )
    events: list[EventLog] = []
    # tick = scheduled + duration -> fires
    process_lifecycle(
        [spot], 12, events, _agents_dict(host, p1, p2), rng=rng
    )
    assert spot.status == SpotStatus.COMPLETED
    assert spot.completed_at_tick == 12
    assert any(e.event_type == "SPOT_COMPLETED" for e in events)


def test_in_progress_with_half_noshow_stays_completed(rng):
    """noshow ratio = exactly 0.5 is NOT > 0.5, so it still COMPLETEs."""
    p1 = _make_agent("A_p1")
    p2 = _make_agent("A_p2")
    spot = _make_spot(
        status=SpotStatus.IN_PROGRESS,
        scheduled_tick=10,
        started_at_tick=10,
        participants=["A_p1", "A_p2"],
        checked_in={"A_p1"},
        noshow={"A_p2"},
        duration=2,
    )
    events: list[EventLog] = []
    process_lifecycle([spot], 12, events, _agents_dict(p1, p2), rng=rng)
    # noshow_ratio = 1/2 = 0.5, threshold is strictly > 0.5
    assert spot.status == SpotStatus.COMPLETED
    assert NOSHOW_DISPUTE_THRESHOLD == 0.5  # sanity


# ---------------------------------------------------------------------------
# 5. IN_PROGRESS -> DISPUTED (noshow > threshold)
# ---------------------------------------------------------------------------


def test_in_progress_with_majority_noshow_transitions_to_disputed(rng):
    spot = _make_spot(
        status=SpotStatus.IN_PROGRESS,
        scheduled_tick=10,
        started_at_tick=10,
        participants=["A_p1", "A_p2", "A_p3"],
        checked_in={"A_p1"},
        noshow={"A_p2", "A_p3"},
        duration=2,
    )
    events: list[EventLog] = []
    process_lifecycle([spot], 12, events, _agents_dict(), rng=rng)
    # noshow_ratio = 2/3 ≈ 0.67 > 0.5
    assert spot.status == SpotStatus.DISPUTED
    assert spot.disputed_at_tick == 12
    assert any(e.event_type == "SPOT_DISPUTED" for e in events)


# ---------------------------------------------------------------------------
# 6. Single-tick transition guarantee: OPEN can NOT race through
#    MATCHED/CONFIRMED in one invocation even if the scheduler says so.
# ---------------------------------------------------------------------------


def test_single_tick_transition_guarantee_open_stays_open(rng):
    """An OPEN spot with age well under timeout but already past its
    scheduled_tick must NOT skip ahead to CONFIRMED/STARTED. Lifecycle
    inspects the ENTRY status only and fires at most one transition."""
    spot = _make_spot(
        status=SpotStatus.OPEN,
        scheduled_tick=1,  # in the past
        created_at_tick=0,
        participants=[],
    )
    events: list[EventLog] = []
    process_lifecycle([spot], 2, events, _agents_dict(), rng=rng)
    # age = 2, nowhere near OPEN_TIMEOUT_TICKS; spot stays OPEN and does NOT
    # teleport through MATCHED/CONFIRMED.
    assert spot.status == SpotStatus.OPEN
    assert not any(
        e.event_type
        in {"SPOT_CONFIRMED", "SPOT_STARTED", "SPOT_COMPLETED"}
        for e in events
    )


def test_single_tick_matched_to_confirmed_then_next_tick_to_started(rng):
    """Two calls produce two steps. One call produces one step."""
    host = _make_agent("A_host")
    p1 = _make_agent("A_p1")
    p2 = _make_agent("A_p2")
    spot = _make_spot(
        status=SpotStatus.MATCHED,
        scheduled_tick=10,
        created_at_tick=5,
        participants=["A_p1", "A_p2"],
    )
    events: list[EventLog] = []

    # Tick 8: MATCHED -> CONFIRMED (gate: 10-8 <= 2)
    process_lifecycle([spot], 8, events, _agents_dict(host, p1, p2), rng=rng)
    assert spot.status == SpotStatus.CONFIRMED
    assert any(e.event_type == "SPOT_CONFIRMED" for e in events)
    # IN_PROGRESS has NOT happened — even though tick 8 < scheduled 10.
    assert spot.started_at_tick is None
    assert not any(e.event_type == "SPOT_STARTED" for e in events)

    # Tick 10: CONFIRMED -> IN_PROGRESS
    process_lifecycle(
        [spot], 10, events, _agents_dict(host, p1, p2), rng=rng
    )
    assert spot.status == SpotStatus.IN_PROGRESS
    assert spot.started_at_tick == 10
    assert any(e.event_type == "SPOT_STARTED" for e in events)


# ---------------------------------------------------------------------------
# 7. No-op: terminal CANCELED / COMPLETED / DISPUTED should be untouched
# ---------------------------------------------------------------------------


def test_terminal_states_are_no_ops(rng):
    canceled = _make_spot(spot_id="S_c", status=SpotStatus.CANCELED)
    completed = _make_spot(spot_id="S_d", status=SpotStatus.COMPLETED)
    disputed = _make_spot(spot_id="S_e", status=SpotStatus.DISPUTED)
    events: list[EventLog] = []
    process_lifecycle(
        [canceled, completed, disputed], 100, events, _agents_dict(), rng=rng
    )
    assert canceled.status == SpotStatus.CANCELED
    assert completed.status == SpotStatus.COMPLETED
    assert disputed.status == SpotStatus.DISPUTED
    assert events == []
