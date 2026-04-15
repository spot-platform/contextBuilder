"""Action decision function — plan §2.6 (Phase 1) + plan §3.5-3.6 (Phase 2).

The engine calls `decide_action` once per active agent per tick. It returns
a tuple `(action, target_spot)` where `action` is one of
`"CREATE_SPOT" | "JOIN_SPOT" | "NO_ACTION"` and `target_spot` is the chosen
spot for `JOIN_SPOT` (else `None`).

All randomness flows through the injected `rng: random.Random` so a fixed
seed produces byte-identical event logs across runs.

# Phase 1 formula (preserved for reference)
#
#     p_create = ( 0.35 * agent.host_score
#                + 0.20 * region_create_affinity(agent, home_region, regions)
#                + 0.25 * agent.social_need
#                - 0.15 * agent.fatigue
#                - 0.10 * recent_host_penalty(agent, tick) )
#
#     p_join   = ( 0.30 * agent.join_score
#                + 0.25 * category_match(agent, best, persona_templates)
#                + 0.20 * agent.social_need
#                - 0.15 * agent.fatigue
#                - 0.10 * budget_penalty(agent, best, persona_templates, regions) )
#
# Phase 1 weights are still used when `decide_action(..., phase=1, ...)`.

Phase 2 (plan §3.5) replaces `p_join` with:

    p_join = ( 0.25 * agent.join_score
             + 0.20 * category_match(agent, best, persona_templates)
             + 0.15 * agent.social_need
             + 0.15 * calc_social_join_modifier(agent, best, agents_by_id, persona_templates)
             + 0.10 * region_create_affinity(agent, best.region_id, region_features)
             - 0.10 * agent.fatigue
             - 0.05 * budget_penalty(agent, best, persona_templates, region_features) )

`p_create` is unchanged in Phase 2.

Both probabilities are clamped to [0, 1]. A single `rng.random()` roll is
compared against the stacked thresholds so the two actions don't require
two independent draws.
"""

from __future__ import annotations

import random

from data.adapters import (
    budget_penalty,
    category_match,
    recent_host_penalty,
    region_create_affinity,
)
from engine._math import clamp
from engine.time_utils import schedule_key
from models import AgentState, Spot


# ---------------------------------------------------------------------------
# Phase 2 helpers (plan §3.5 / §3.6)
# ---------------------------------------------------------------------------


def avg_interest_overlap(
    agent: AgentState,
    participant_ids: list[str],
    agents_by_id: dict[str, AgentState],
    persona_templates: dict,
) -> float:
    """Return the mean Jaccard similarity of `interest_categories` between
    `agent` and every resolved participant in `participant_ids`.

    Empty participant list -> 0.0. Unknown participant ids are skipped. If
    both category sets are empty the pair contributes 0.0 (undefined
    similarity, no bonus).

    `persona_templates` is accepted so future revisions can score partial
    overlaps through shared persona category axes without changing the
    signature — unused in the Phase 2 implementation.
    """
    del persona_templates  # reserved for richer similarity in future phases

    if not participant_ids:
        return 0.0

    agent_set = set(agent.interest_categories or ())

    total = 0.0
    count = 0
    for pid in participant_ids:
        other = agents_by_id.get(pid)
        if other is None:
            continue
        other_set = set(other.interest_categories or ())
        union = agent_set | other_set
        if not union:
            similarity = 0.0
        else:
            similarity = len(agent_set & other_set) / len(union)
        total += similarity
        count += 1

    if count == 0:
        return 0.0
    return total / count


def calc_social_join_modifier(
    agent: AgentState,
    spot: Spot,
    agents_by_id: dict[str, AgentState],
    persona_templates: dict,
) -> float:
    """Plan §3.5 — social modifier on `p_join`.

    Components:
      * FOMO bonus       : +0.15 once fill_rate >= 0.7
      * Host trust       : +/- 0.10 * (host.trust_score - 0.5)
      * Category affinity: +0.10 * avg_interest_overlap(...)

    Returns a float roughly in `[-0.05, 0.35]`. When the spot has no
    participants yet the function returns `0.0` (no baseline pull, no push).
    """
    if not spot.participants:
        return 0.0

    fill_rate = len(spot.participants) / spot.capacity if spot.capacity else 0.0
    fomo_bonus = 0.15 if fill_rate >= 0.7 else 0.0

    host = agents_by_id.get(spot.host_agent_id)
    if host is not None:
        trust_modifier = 0.10 * (host.trust_score - 0.5)
    else:
        trust_modifier = 0.0

    shared = avg_interest_overlap(
        agent, spot.participants, agents_by_id, persona_templates
    )
    affinity_bonus = 0.10 * shared

    return fomo_bonus + trust_modifier + affinity_bonus


def _snap_to_preferred_time(agent: AgentState, candidate: int) -> int:
    """Nudge `candidate` within +/-6 ticks to land on the highest-weighted
    schedule_key. Ties break to the smallest tick in the window.
    """
    best_tick = candidate
    best_weight = agent.schedule_weights.get(schedule_key(candidate), 0.0)

    # Search in deterministic order (smallest -> largest) so ties resolve
    # toward the earliest candidate tick.
    for offset in range(-6, 7):
        t = candidate + offset
        if t < 0:
            continue
        w = agent.schedule_weights.get(schedule_key(t), 0.0)
        if w > best_weight:
            best_weight = w
            best_tick = t
    return best_tick


def pick_scheduled_tick(
    agent: AgentState, current_tick: int, rng: random.Random
) -> int:
    """Plan §3.6 — persona-aware lead-time + schedule snap.

    Lead-time distribution by persona:
      * `spontaneous`, `night_social`       -> rng.randint(6, 24)
      * `planner`, `weekend_explorer`       -> rng.randint(24, 72)
      * everything else                     -> rng.randint(12, 48)

    The raw candidate is then snapped to the best `schedule_weight` tick
    inside a +/-6 window so hosts naturally aim for their preferred slot.
    """
    persona = agent.persona_type
    if persona in ("spontaneous", "night_social"):
        lead_hours = rng.randint(6, 24)
    elif persona in ("planner", "weekend_explorer"):
        lead_hours = rng.randint(24, 72)
    else:
        lead_hours = rng.randint(12, 48)

    candidate = current_tick + lead_hours
    return _snap_to_preferred_time(agent, candidate)


# ---------------------------------------------------------------------------
# Matchable-spot filter (unchanged from Phase 1)
# ---------------------------------------------------------------------------


def find_matchable_spots(
    agent: AgentState,
    open_spots: list[Spot],
    *,
    persona_templates: dict,
    agents_by_id: dict[str, AgentState] | None = None,
    phase: int = 1,
) -> list[Spot]:
    """Filter + sort spots this agent could join this tick.

    Filter rules (plan §2.6):
      * spot.region_id must be one of agent.active_regions
      * still has headroom (len(participants) < capacity)
      * agent is not the host
      * agent hasn't already joined

    Sort key (descending):
      1. category_match score
      2. current participant count (prefer nearly-full spots)

    Phase 3 (`phase>=3`, plan §4.6 criterion 4): when `agents_by_id` is
    provided, the category match score is multiplied by
    `0.5 + 0.5 * host.trust_score` so low-trust hosts naturally drift to
    the back of the candidate list. The Phase 1 / Phase 2 sort is
    untouched (this branch is gated on `phase >= 3`).
    """
    candidates: list[Spot] = []
    for spot in open_spots:
        if spot.region_id not in agent.active_regions:
            continue
        if len(spot.participants) >= spot.capacity:
            continue
        if spot.host_agent_id == agent.agent_id:
            continue
        if agent.agent_id in spot.participants:
            continue
        candidates.append(spot)

    if phase >= 3 and agents_by_id is not None:
        def _sort_key(s: Spot) -> tuple[float, int]:
            base = category_match(agent, s, persona_templates)
            host = agents_by_id.get(s.host_agent_id)
            host_trust = host.trust_score if host is not None else 0.5
            effective = base * (0.5 + 0.5 * host_trust)
            return (effective, len(s.participants))
    else:
        def _sort_key(s: Spot) -> tuple[float, int]:
            return (
                category_match(agent, s, persona_templates),
                len(s.participants),
            )

    candidates.sort(key=_sort_key, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# decide_action (Phase 1 default; Phase 2 when phase >= 2)
# ---------------------------------------------------------------------------


def decide_action(
    agent: AgentState,
    tick: int,
    open_spots: list[Spot],
    *,
    rng: random.Random,
    region_features: dict,
    persona_templates: dict,
    agents_by_id: dict[str, AgentState] | None = None,
    phase: int = 1,
) -> tuple[str, Spot | None]:
    """Decide what `agent` does this tick.

    Returns `(action, target_spot)`:
      * `("CREATE_SPOT", None)`  — agent will host a new spot
      * `("JOIN_SPOT", spot)`     — agent will join `spot`
      * `("NO_ACTION", None)`     — agent sits this tick out

    `phase=1` preserves the exact Phase 1 rng-draw sequence so
    `python main.py --phase 1` produces byte-identical logs. `phase>=2`
    swaps in the §3.5 p_join weights plus the social modifier. Both phases
    still draw exactly the same number of random numbers in the same order
    within a single call — a Phase 1 run therefore never calls
    `calc_social_join_modifier` and never touches `agents_by_id`.
    """
    # --- 1. Time-of-day activation gate -------------------------------------
    time_weight = agent.schedule_weights.get(schedule_key(tick), 0.1)
    if rng.random() > time_weight:
        return ("NO_ACTION", None)

    # --- 2. p_create (unchanged across phases) ------------------------------
    p_create = (
        0.35 * agent.host_score
        + 0.20 * region_create_affinity(
            agent, agent.home_region_id, region_features
        )
        + 0.25 * agent.social_need
        - 0.15 * agent.fatigue
        - 0.10 * recent_host_penalty(agent, tick)
    )
    p_create = clamp(p_create)

    # --- 3. p_join -----------------------------------------------------------
    matchable = find_matchable_spots(
        agent,
        open_spots,
        persona_templates=persona_templates,
        agents_by_id=agents_by_id,
        phase=phase,
    )
    p_join = 0.0
    best: Spot | None = None
    if matchable:
        best = matchable[0]
        if phase >= 2:
            social_mod = 0.0
            if agents_by_id is not None:
                social_mod = calc_social_join_modifier(
                    agent, best, agents_by_id, persona_templates
                )
            region_aff = region_create_affinity(
                agent, best.region_id, region_features
            )
            p_join = (
                0.25 * agent.join_score
                + 0.20 * category_match(agent, best, persona_templates)
                + 0.15 * agent.social_need
                + 0.15 * social_mod
                + 0.10 * region_aff
                - 0.10 * agent.fatigue
                - 0.05 * budget_penalty(
                    agent, best, persona_templates, region_features
                )
            )
        else:
            # Phase 1 formula — DO NOT TOUCH (see module docstring).
            p_join = (
                0.30 * agent.join_score
                + 0.25 * category_match(agent, best, persona_templates)
                + 0.20 * agent.social_need
                - 0.15 * agent.fatigue
                - 0.10 * budget_penalty(
                    agent, best, persona_templates, region_features
                )
            )
        p_join = clamp(p_join)

    # --- 4. Single uniform roll against the two thresholds ------------------
    roll = rng.random()
    if roll < p_create and agent.current_state == "idle":
        return ("CREATE_SPOT", None)
    if matchable and best is not None and roll < p_create + p_join:
        return ("JOIN_SPOT", best)

    # --- 5. Phase 3 background actions --------------------------------------
    # `VIEW_FEED` and `SAVE_SPOT` only fire when the agent would otherwise
    # NO_ACTION, so they don't compete with CREATE_SPOT / JOIN_SPOT. Each
    # rolls its own draw (independent of the stacked `roll` above) so the
    # rng draw sequence in Phase 1 / Phase 2 is unaffected.
    if phase >= 3:
        p_view_feed = clamp(0.10 * agent.social_need)
        if rng.random() < p_view_feed:
            return ("VIEW_FEED", None)

        p_save_spot = clamp(0.05 * agent.join_score)
        if matchable and best is not None and rng.random() < p_save_spot:
            return ("SAVE_SPOT", best)

    return ("NO_ACTION", None)
