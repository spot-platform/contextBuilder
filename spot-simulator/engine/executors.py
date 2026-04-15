"""Action executors — pure helpers that mutate agent/spot state.

Kept separate from `decision.py` so tests can exercise side effects
independently of probability math. All randomness is injected via
`rng: random.Random` for determinism.

Phase 1 actions:
    execute_create_spot, execute_join_spot, try_auto_match

Phase 2 additions (plan §3.2):
    execute_cancel_join, execute_check_in, execute_no_show

COMPLETE_SPOT is intentionally NOT an agent action — plan §3.4's lifecycle
processor closes spots automatically at `scheduled_tick + duration`, so the
action count from §3.2 is a high-water mark that Phase 2 does not hit.
"""

from __future__ import annotations

import random
from typing import Iterator

from models import AgentState, Spot, SpotStatus


def execute_create_spot(
    agent: AgentState,
    tick: int,
    *,
    rng: random.Random,
    region_features: dict,
    persona_templates: dict,
    spot_counter: Iterator[int],
    phase: int = 1,
) -> Spot:
    """Create an OPEN spot hosted by `agent` and mutate the host in place.

    Phase 1 (`phase=1`, default):
      * `scheduled_tick` is `tick + rng.randint(6, 36)` — identical to the
        original MVP path so byte-level reproducibility of Phase 1 logs is
        preserved.

    Phase 2 (`phase>=2`):
      * `scheduled_tick` delegates to
        `engine.decision.pick_scheduled_tick(agent, tick, rng)` which picks
        a persona-aware lead time and snaps to the agent's preferred
        schedule slot (plan §3.6).

    `capacity` draws from `[3, 4, 4, 5]` (weighted toward 4). `category`
    is a random pick from `agent.interest_categories` with a `"food"`
    fallback when the list is empty. These draws are identical across
    phases so Phase 1 rng consumption is unaffected by the phase flag.
    """
    del region_features  # reserved for region/category biasing
    del persona_templates  # reserved for duration lookup

    spot_id = f"S_{next(spot_counter):04d}"
    region_id = rng.choice(agent.active_regions)

    if agent.interest_categories:
        category = rng.choice(agent.interest_categories)
    else:
        category = "food"

    capacity = rng.choice([3, 4, 4, 5])
    min_participants = 2

    if phase >= 2:
        # Local import to avoid a decision<->executors import cycle at
        # module load (decision.py imports nothing from executors.py today,
        # but a top-level import would still leave a dangling dependency if
        # that ever changes).
        from engine.decision import pick_scheduled_tick

        scheduled_tick = pick_scheduled_tick(agent, tick, rng)
    else:
        lead = rng.randint(6, 36)
        scheduled_tick = tick + lead

    spot = Spot(
        spot_id=spot_id,
        host_agent_id=agent.agent_id,
        region_id=region_id,
        category=category,
        capacity=capacity,
        min_participants=min_participants,
        scheduled_tick=scheduled_tick,
        created_at_tick=tick,
        status=SpotStatus.OPEN,
        participants=[],
    )

    # Host-side state mutation.
    agent.current_state = "idle"
    agent.last_action_tick = tick
    agent.hosted_spots.append(spot_id)

    return spot


def execute_join_spot(agent: AgentState, spot: Spot, tick: int) -> bool:
    """Add `agent` to `spot.participants` if capacity allows.

    Returns `True` when the join succeeds, `False` otherwise. Does NOT mutate
    `agent.current_state` — Phase 1 lets an idle agent join multiple spots
    across ticks, and lifecycle pinning moves to Phase 2.
    """
    if len(spot.participants) >= spot.capacity:
        return False
    if agent.agent_id in spot.participants:
        return False
    if spot.host_agent_id == agent.agent_id:
        return False

    spot.participants.append(agent.agent_id)
    agent.joined_spots.append(spot.spot_id)
    agent.last_action_tick = tick
    return True


def try_auto_match(spot: Spot, *, phase: int = 1) -> bool:
    """Flip an OPEN spot to MATCHED once the match threshold is reached.

    Phase 1 (`phase=1`, default):
        Matches as soon as `len(participants) >= min_participants`, literal
        plan §3.3. Preserved byte-for-byte for Phase 1 log reproducibility.

    Phase 2 (`phase>=2`):
        Delays auto-match until the spot is nearly full — specifically
        `max(capacity - 1, min_participants)` joiners — so the FOMO
        modifier (plan §3.5, threshold fill_rate >= 0.7) has room to fire
        on partially-filled spots before they flip to MATCHED. A capacity-4
        spot now needs 3 joiners (fill=0.75), capacity-5 needs 4 (fill=0.8).

    Returns `True` when the transition happens, `False` otherwise (already
    matched, still below threshold, etc.).
    """
    if spot.status != SpotStatus.OPEN:
        return False

    if phase >= 2:
        required = max(spot.capacity - 1, spot.min_participants)
    else:
        required = spot.min_participants

    if len(spot.participants) < required:
        return False
    spot.status = SpotStatus.MATCHED
    return True


# ---------------------------------------------------------------------------
# Phase 2 action executors (plan §3.2)
# ---------------------------------------------------------------------------


def execute_cancel_join(agent: AgentState, spot: Spot, tick: int) -> bool:
    """Remove `agent` from `spot.participants` while the spot is still
    cancellable (OPEN or MATCHED — CONFIRMED and later is too late).

    Also removes the spot from `agent.joined_spots`. Returns True on
    success. Does not emit events — runner.py owns event logging.
    """
    if spot.status not in (SpotStatus.OPEN, SpotStatus.MATCHED):
        return False
    if agent.agent_id not in spot.participants:
        return False

    spot.participants.remove(agent.agent_id)
    # Clean up the agent-side trackers. joined_spots is a list — strip
    # every occurrence so a double-join (shouldn't happen, but cheap to be
    # safe) doesn't leave a dangling reference.
    agent.joined_spots = [s for s in agent.joined_spots if s != spot.spot_id]
    agent.last_action_tick = tick

    # A MATCHED spot that drops below min_participants should fall back to
    # OPEN so others can still join. This keeps lifecycle invariants clean.
    if (
        spot.status == SpotStatus.MATCHED
        and len(spot.participants) < spot.min_participants
    ):
        spot.status = SpotStatus.OPEN

    return True


def execute_check_in(agent: AgentState, spot: Spot, tick: int) -> bool:
    """Mark `agent` as checked-in for `spot`.

    Requires the spot to be IN_PROGRESS. Mutates both sides:
      * `spot.checked_in.add(agent_id)`
      * `agent.checked_in_spots.add(spot_id)`
      * clears the spot from `agent.confirmed_spots`
    """
    if spot.status != SpotStatus.IN_PROGRESS:
        return False
    if agent.agent_id not in spot.participants and agent.agent_id != spot.host_agent_id:
        return False

    spot.checked_in.add(agent.agent_id)
    agent.checked_in_spots.add(spot.spot_id)
    if spot.spot_id in agent.confirmed_spots:
        agent.confirmed_spots.remove(spot.spot_id)
    agent.last_action_tick = tick
    return True


def execute_no_show(agent: AgentState, spot: Spot, tick: int) -> None:
    """Mark `agent` as NO_SHOW for `spot`.

    Mutates both sides:
      * `spot.noshow.add(agent_id)`
      * `agent.noshow_spots.add(spot_id)`
      * clears the spot from `agent.confirmed_spots`
    """
    spot.noshow.add(agent.agent_id)
    agent.noshow_spots.add(spot.spot_id)
    if spot.spot_id in agent.confirmed_spots:
        agent.confirmed_spots.remove(spot.spot_id)
    agent.last_action_tick = tick


# ---------------------------------------------------------------------------
# Phase 3 action executors (plan §4.2)
# ---------------------------------------------------------------------------


def execute_write_review(
    agent: AgentState, spot: Spot, tick: int, satisfaction: float
) -> None:
    """Mark a proactive review write — append spot_id to `agent.review_spots`
    and bump `spot.review_count`. Does not emit events; runner.py owns
    event logging.

    Note: most reviews are emitted from `engine.settlement.process_settlement`
    directly. This executor exists for the (rare) decision-driven proactive
    review path described in plan §4.2.
    """
    del satisfaction  # accepted for symmetry with settlement-side review path
    if spot.spot_id not in agent.review_spots:
        agent.review_spots.append(spot.spot_id)
    spot.review_count += 1
    agent.last_action_tick = tick


def execute_save_spot(agent: AgentState, spot: Spot, tick: int) -> None:
    """Bookmark `spot` into `agent.saved_spots` (no-op if already saved)."""
    if spot.spot_id not in agent.saved_spots:
        agent.saved_spots.append(spot.spot_id)
    agent.last_action_tick = tick


def execute_view_feed(agent: AgentState, tick: int) -> None:
    """`VIEW_FEED` — pure behavioural signal, no state mutation beyond
    `last_action_tick`. The runner emits the EventLog row."""
    agent.last_action_tick = tick
