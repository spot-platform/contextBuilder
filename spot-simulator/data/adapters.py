"""Engine-facing adapters.

All functions in this module are **pure**: they take loaded data dicts as
arguments and return a float in the documented range. The engine is expected
to pass the already-loaded ``region_features`` / ``persona_templates`` dicts
so that nothing here touches disk.

Contract summary (engine -> adapter):

    region_create_affinity(agent, region_id, region_features) -> 0..1
    category_match(agent, spot, persona_templates)            -> 0..1
    budget_penalty(agent, spot, persona_templates)            -> 0..1
    recent_host_penalty(agent, tick)                          -> 0..1

If a region_id is missing from region_features, ``region_create_affinity``
returns 0.0 and emits a warning via the ``warnings`` module so the tick loop
can keep running.
"""

from __future__ import annotations

import warnings
from typing import Any

# ---- Tunable constants (Phase 1 values) -----------------------------------

#: Maximum budget-level gap used as the denominator of the budget penalty.
#: With levels 1..3 the worst mismatch is |1 - 3| = 2, so dividing by 3 keeps
#: the penalty in [0, 1) and leaves headroom if we later widen the scale.
BUDGET_PENALTY_MAX_GAP = 3

#: Window (in ticks = hours) during which a recent host is penalised from
#: hosting again. Matches the "12 hour cooldown" rule in plan §2.6.
RECENT_HOST_WINDOW = 12

#: Flat penalty value returned when the cooldown rule fires.
RECENT_HOST_PENALTY_VALUE = 0.5


# ---- Adapters -------------------------------------------------------------


def region_create_affinity(
    agent: Any,
    region_id: str,
    region_features: dict[str, dict[str, Any]],
) -> float:
    """Return the region's ``spot_create_affinity`` in [0, 1].

    If ``region_id`` is unknown, returns 0.0 and warns. This keeps the tick
    loop deterministic instead of throwing mid-simulation.
    """
    entry = region_features.get(region_id)
    if entry is None:
        warnings.warn(
            f"region_create_affinity: unknown region_id '{region_id}' "
            f"(agent={getattr(agent, 'agent_id', '?')}) -> 0.0",
            stacklevel=2,
        )
        return 0.0
    value = entry.get("spot_create_affinity", 0.0)
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def category_match(
    agent: Any,
    spot: Any,
    persona_templates: dict[str, dict[str, Any]],
) -> float:
    """Return 1.0 if the spot's category overlaps the agent's interests, else 0.0.

    Phase 1 intentionally treats this as a boolean signal. Phase 2+ can
    reuse this hook for partial-match scoring (e.g. cosine over a category
    embedding) without changing the adapter signature.

    ``persona_templates`` is accepted for forward-compatibility; not used
    in the Phase 1 implementation because ``agent.interest_categories`` is
    already materialised on the AgentState.
    """
    del persona_templates  # unused in Phase 1
    spot_category = getattr(spot, "category", None)
    if spot_category is None:
        return 0.0
    interests = getattr(agent, "interest_categories", None) or []
    return 1.0 if spot_category in interests else 0.0


def budget_penalty(
    agent: Any,
    spot: Any,
    persona_templates: dict[str, dict[str, Any]],
    region_features: dict[str, dict[str, Any]] | None = None,
) -> float:
    """Return a 0..1 penalty for budget mismatch between agent and spot.

    Phase 1 rule:
        penalty = clamp(|agent.budget_level - spot_budget_level| / MAX_GAP, 0, 1)

    Phase 1 Spots don't carry ``budget_level`` yet, so we derive the spot's
    effective budget from ``region_features[spot.region_id].budget_avg_level``.
    If that lookup fails we fall back to the agent's own budget (penalty=0)
    to avoid artificially blocking actions.
    """
    del persona_templates  # unused in Phase 1
    agent_budget = getattr(agent, "budget_level", None)
    if agent_budget is None:
        return 0.0

    spot_budget = getattr(spot, "budget_level", None)
    if spot_budget is None and region_features is not None:
        region_id = getattr(spot, "region_id", None)
        entry = region_features.get(region_id) if region_id else None
        if entry is not None:
            spot_budget = entry.get("budget_avg_level")

    if spot_budget is None:
        return 0.0

    gap = abs(float(agent_budget) - float(spot_budget))
    penalty = gap / BUDGET_PENALTY_MAX_GAP
    if penalty < 0.0:
        return 0.0
    if penalty > 1.0:
        return 1.0
    return penalty


def recent_host_penalty(agent: Any, tick: int) -> float:
    """Return a flat penalty if the agent hosted a spot very recently.

    Phase 1 rule:
        * if agent has no hosted_spots               -> 0.0
        * if tick - agent.last_action_tick < WINDOW  -> RECENT_HOST_PENALTY_VALUE
        * otherwise                                  -> 0.0
    """
    hosted = getattr(agent, "hosted_spots", None) or []
    if not hosted:
        return 0.0
    last = getattr(agent, "last_action_tick", -1)
    if last < 0:
        return 0.0
    if (tick - last) < RECENT_HOST_WINDOW:
        return RECENT_HOST_PENALTY_VALUE
    return 0.0
