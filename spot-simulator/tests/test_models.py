"""Phase 1 model tests — dataclasses, decay math, event factory."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from engine.decay import (
    CREATE_FATIGUE_DELTA,
    CREATE_SOCIAL_DELTA,
    FATIGUE_DECAY_MULT,
    FATIGUE_DECAY_SUB,
    SOCIAL_NEED_GROW,
    after_create_spot,
    decay_fatigue,
    grow_social_need,
)
from models import (
    AgentState,
    EventLog,
    Spot,
    SpotStatus,
    make_event,
    reset_event_counter,
    serialize_event,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_agent(**over) -> AgentState:
    defaults: dict = dict(
        agent_id="A_test",
        persona_type="night_social",
        home_region_id="emd_yeonmu",
        active_regions=["emd_yeonmu", "emd_jangan"],
        interest_categories=["food", "cafe"],
        host_score=0.6,
        join_score=0.6,
        fatigue=0.5,
        social_need=0.5,
        current_state="idle",
        schedule_weights={"weekday_evening": 0.9},
        budget_level=2,
    )
    defaults.update(over)
    return AgentState(**defaults)


def _make_spot(**over) -> Spot:
    defaults: dict = dict(
        spot_id="S_0001",
        host_agent_id="A_host",
        region_id="emd_yeonmu",
        category="food",
        capacity=4,
        min_participants=2,
        scheduled_tick=20,
        created_at_tick=10,
    )
    defaults.update(over)
    return Spot(**defaults)


@pytest.fixture(autouse=True)
def _reset_counter():
    reset_event_counter(1)
    yield


# ---------------------------------------------------------------------------
# AgentState construction
# ---------------------------------------------------------------------------


def test_agent_state_requires_all_phase1_fields():
    a = _make_agent()
    assert a.agent_id == "A_test"
    assert a.persona_type == "night_social"
    assert a.home_region_id == "emd_yeonmu"
    assert a.active_regions == ["emd_yeonmu", "emd_jangan"]
    assert a.interest_categories == ["food", "cafe"]
    assert 0.0 <= a.host_score <= 1.0
    assert 0.0 <= a.join_score <= 1.0
    assert a.current_state == "idle"
    assert a.budget_level == 2
    # Default-backed tracking fields.
    assert a.last_action_tick == -1
    assert a.hosted_spots == []
    assert a.joined_spots == []


# ---------------------------------------------------------------------------
# Drift sentinels — split per phase so future phases can drift independently.
#
# Phase 1 sentinel (PHASE1_AGENT_FIELDS) locks down the original field set.
# Phase 2 sentinel (PHASE2_AGENT_FIELDS) adds the trust/lifecycle additions.
# If Phase 3 lands, add a `PHASE3_AGENT_FIELDS` constant and a third test.
# The actual dataclass field set must equal PHASE1 ∪ PHASE2 ∪ ... at every
# moment, so a regression in either phase's fields is detected immediately.
# ---------------------------------------------------------------------------

PHASE1_AGENT_FIELDS: set[str] = {
    "agent_id",
    "persona_type",
    "home_region_id",
    "active_regions",
    "interest_categories",
    "host_score",
    "join_score",
    "fatigue",
    "social_need",
    "current_state",
    "schedule_weights",
    "budget_level",
    "last_action_tick",
    "hosted_spots",
    "joined_spots",
}

PHASE2_AGENT_FIELDS: set[str] = {
    "trust_score",
    "prev_trust",
    "confirmed_spots",
    "checked_in_spots",
    "noshow_spots",
}

PHASE3_AGENT_FIELDS: set[str] = {
    "trust_threshold",
    "review_spots",
    "saved_spots",
    "satisfaction_history",
}

# Phase Peer-A (peer pivot §2-5) — append-only on top of Phase 1~3. Existing
# Phase 1/2/3 drift sentinels must continue to pass; the "unexpected field"
# check below is widened to accept these new peer-pivot fields.
PHASE_PEER_AGENT_FIELDS: set[str] = {
    "skills",
    "assets",
    "relationships",
    "role_preference",
}


def test_agent_state_phase1_field_set():
    """Phase 1 sentinel: every Phase 1 column must still exist in AgentState.

    Append-only rule: Phase 2/3 may ADD fields, never remove or rename Phase 1
    ones. We check `PHASE1_AGENT_FIELDS ⊆ actual` instead of equality so the
    same assertion survives future phase expansions.
    """
    actual = set(AgentState.__annotations__.keys())
    missing = PHASE1_AGENT_FIELDS - actual
    assert not missing, f"Phase 1 AgentState fields removed/renamed: {missing}"


def test_agent_state_phase2_field_set():
    """Phase 2 sentinel: Phase 2 additions must be present AND no unexpected
    drift beyond the union of known-phase fields."""
    actual = set(AgentState.__annotations__.keys())
    missing_p2 = PHASE2_AGENT_FIELDS - actual
    assert not missing_p2, f"Phase 2 AgentState fields missing: {missing_p2}"

    # Combined contract — everything in AgentState must belong to a known
    # phase's field set. Extra fields flag a contract drift that the column
    # contract hasn't caught up with yet. Phase Peer-A (§2-5 peer pivot) is
    # included so legacy Phase 1/2/3 drift sentinels pass under append-only.
    known = (
        PHASE1_AGENT_FIELDS
        | PHASE2_AGENT_FIELDS
        | PHASE3_AGENT_FIELDS
        | PHASE_PEER_AGENT_FIELDS
    )
    unexpected = actual - known
    assert not unexpected, (
        f"AgentState has fields not declared in any phase contract: {unexpected}. "
        f"Update PHASE1/PHASE2/PHASE3/PHASE_PEER_AGENT_FIELDS or column_contract.md."
    )


def test_agent_state_phase3_field_set():
    """Phase 3 sentinel: Phase 3 additions (plan §4) must be present AND no
    unexpected drift beyond the union of known-phase fields."""
    actual = set(AgentState.__annotations__.keys())
    missing_p3 = PHASE3_AGENT_FIELDS - actual
    assert not missing_p3, f"Phase 3 AgentState fields missing: {missing_p3}"

    known = (
        PHASE1_AGENT_FIELDS
        | PHASE2_AGENT_FIELDS
        | PHASE3_AGENT_FIELDS
        | PHASE_PEER_AGENT_FIELDS
    )
    unexpected = actual - known
    assert not unexpected, (
        f"AgentState has fields not declared in any phase contract: {unexpected}. "
        f"Update PHASE1/PHASE2/PHASE3/PHASE_PEER_AGENT_FIELDS or column_contract.md."
    )


def test_agent_state_phase_peer_field_set():
    """Phase Peer-A sentinel: peer-pivot §2-5 fields must be present.

    Append-only contract: `PHASE_PEER_AGENT_FIELDS ⊆ actual`. Does not
    constrain the absence of future phases; any additional phase drift
    sentinel should OR-in its own constant to the `known` set above and add
    a symmetric sentinel test here.
    """
    actual = set(AgentState.__annotations__.keys())
    missing_peer = PHASE_PEER_AGENT_FIELDS - actual
    assert not missing_peer, (
        f"Phase Peer-A AgentState fields missing: {missing_peer}"
    )


# ---------------------------------------------------------------------------
# Decay / growth boundary behaviour
# ---------------------------------------------------------------------------


def test_decay_fatigue_from_half():
    a = _make_agent(fatigue=0.5)
    decay_fatigue(a)
    expected = max(0.0, 0.5 * FATIGUE_DECAY_MULT - FATIGUE_DECAY_SUB)
    assert a.fatigue == pytest.approx(expected)


def test_decay_fatigue_from_zero_stays_zero():
    a = _make_agent(fatigue=0.0)
    decay_fatigue(a)
    assert a.fatigue == 0.0


def test_decay_fatigue_never_negative():
    a = _make_agent(fatigue=0.01)
    for _ in range(20):
        decay_fatigue(a)
    assert a.fatigue == 0.0


def test_grow_social_need_clamps_at_one():
    a = _make_agent(social_need=0.99)
    grow_social_need(a)
    assert a.social_need == 1.0
    grow_social_need(a)
    assert a.social_need == 1.0


def test_grow_social_need_step():
    a = _make_agent(social_need=0.4)
    grow_social_need(a)
    assert a.social_need == pytest.approx(0.4 + SOCIAL_NEED_GROW)


def test_after_create_spot_flows():
    a = _make_agent(fatigue=0.2, social_need=0.5)
    after_create_spot(a)
    assert a.fatigue == pytest.approx(
        min(1.0, max(0.0, 0.2 + CREATE_FATIGUE_DELTA))
    )
    assert a.social_need == pytest.approx(
        min(1.0, max(0.0, 0.5 + CREATE_SOCIAL_DELTA))
    )
    assert a.fatigue > 0.2
    assert a.social_need < 0.5


# ---------------------------------------------------------------------------
# SpotStatus enum / StrEnum equality
# ---------------------------------------------------------------------------


def test_spot_status_streq():
    assert SpotStatus.OPEN == "OPEN"
    assert SpotStatus.MATCHED == "MATCHED"
    assert SpotStatus.CANCELED == "CANCELED"
    spot = _make_spot(status=SpotStatus.OPEN)
    assert spot.status == "OPEN"  # StrEnum ergonomics


PHASE1_SPOT_STATUSES: set[str] = {"OPEN", "MATCHED", "CANCELED"}
PHASE2_SPOT_STATUSES: set[str] = {
    "CONFIRMED",
    "IN_PROGRESS",
    "COMPLETED",
    "DISPUTED",
}
PHASE3_SPOT_STATUSES: set[str] = {
    "SETTLED",
    "FORCE_SETTLED",
}


def test_spot_phase1_enum_values():
    """Phase 1 enum drift: every Phase 1 status must still exist as an enum
    value. Append-only; Phase 2 additions are allowed."""
    actual = {s.value for s in SpotStatus}
    missing = PHASE1_SPOT_STATUSES - actual
    assert not missing, f"Phase 1 SpotStatus values removed/renamed: {missing}"


def test_spot_phase2_enum_values():
    """Phase 2 enum drift: Phase 2 additions must be present AND no unknown
    values have leaked in beyond the union of known-phase statuses."""
    actual = {s.value for s in SpotStatus}
    missing_p2 = PHASE2_SPOT_STATUSES - actual
    assert not missing_p2, f"Phase 2 SpotStatus values missing: {missing_p2}"

    known = PHASE1_SPOT_STATUSES | PHASE2_SPOT_STATUSES | PHASE3_SPOT_STATUSES
    unexpected = actual - known
    assert not unexpected, (
        f"SpotStatus has values not declared in any phase contract: {unexpected}"
    )


def test_spot_phase3_enum_values():
    """Phase 3 enum drift: Phase 3 additions (plan §4) must be present AND no
    unknown values have leaked in beyond the union of known-phase statuses."""
    actual = {s.value for s in SpotStatus}
    missing_p3 = PHASE3_SPOT_STATUSES - actual
    assert not missing_p3, f"Phase 3 SpotStatus values missing: {missing_p3}"

    known = PHASE1_SPOT_STATUSES | PHASE2_SPOT_STATUSES | PHASE3_SPOT_STATUSES
    unexpected = actual - known
    assert not unexpected, (
        f"SpotStatus has values not declared in any phase contract: {unexpected}"
    )


# ---------------------------------------------------------------------------
# make_event — region_id resolution order
# ---------------------------------------------------------------------------


def test_make_event_agent_only_uses_home_region():
    a = _make_agent(home_region_id="emd_yeonmu")
    e = make_event(5, "NO_ACTION", agent=a)
    assert e.region_id == "emd_yeonmu"
    assert e.agent_id == "A_test"
    assert e.spot_id is None


def test_make_event_spot_only_uses_spot_region():
    s = _make_spot(region_id="emd_sinchon")
    e = make_event(5, "SPOT_MATCHED", spot=s)
    assert e.region_id == "emd_sinchon"
    assert e.agent_id is None
    assert e.spot_id == "S_0001"


def test_make_event_agent_and_spot_prefers_spot_region():
    a = _make_agent(home_region_id="emd_yeonmu")
    s = _make_spot(region_id="emd_sinchon")
    e = make_event(5, "CREATE_SPOT", agent=a, spot=s)
    assert e.region_id == "emd_sinchon"  # spot wins
    assert e.agent_id == "A_test"
    assert e.spot_id == "S_0001"


def test_make_event_neither_agent_nor_spot_region_none():
    e = make_event(5, "NO_ACTION")
    assert e.region_id is None
    assert e.agent_id is None
    assert e.spot_id is None
    assert e.payload == {}


def test_make_event_explicit_region_override_wins():
    a = _make_agent(home_region_id="emd_yeonmu")
    s = _make_spot(region_id="emd_sinchon")
    e = make_event(5, "SPOT_MATCHED", agent=a, spot=s, region_id="emd_jangan")
    assert e.region_id == "emd_jangan"


# ---------------------------------------------------------------------------
# Event id counter + serialization round-trip
# ---------------------------------------------------------------------------


def test_reset_event_counter_produces_deterministic_ids():
    reset_event_counter(1)
    e1 = make_event(0, "NO_ACTION")
    e2 = make_event(0, "NO_ACTION")
    assert e1.event_id == 1
    assert e2.event_id == 2

    reset_event_counter(1)
    e3 = make_event(0, "NO_ACTION")
    assert e3.event_id == 1


def test_serialize_event_is_single_line_json():
    e = make_event(10, "CREATE_SPOT", payload={"test": True})
    line = serialize_event(e)
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["tick"] == 10
    assert parsed["event_type"] == "CREATE_SPOT"
    assert parsed["payload"] == {"test": True}
