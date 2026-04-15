"""Fatigue / social_need decay and post-action deltas — plan §2.4.

All tunable parameters are declared as module-level UPPER_CASE constants so
sim-analyst-qa can import and override them from a tuning harness without
editing function bodies.
"""

from __future__ import annotations

from models.agent import AgentState

# ---------------------------------------------------------------------------
# Tunable parameters (plan §2.4)
# ---------------------------------------------------------------------------

# Per-tick natural decay / growth
FATIGUE_DECAY_MULT: float = 0.92   # multiplicative component
FATIGUE_DECAY_SUB: float = 0.02    # subtractive component after multiply
SOCIAL_NEED_GROW: float = 0.03     # additive per tick

# Post-action deltas (applied AFTER the action resolves)
CREATE_FATIGUE_DELTA: float = 0.25
CREATE_SOCIAL_DELTA: float = -0.15

JOIN_FATIGUE_DELTA: float = 0.15
JOIN_SOCIAL_DELTA: float = -0.30

COMPLETE_FATIGUE_DELTA: float = 0.20
COMPLETE_SOCIAL_DELTA: float = -0.40


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clamp01(x: float) -> float:
    """Clamp a float to the closed unit interval [0, 1]."""

    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


# ---------------------------------------------------------------------------
# Per-tick natural changes
# ---------------------------------------------------------------------------


def decay_fatigue(agent: AgentState) -> None:
    """Resting recovers fatigue (multiplicative then subtractive, floored at 0)."""

    agent.fatigue = max(0.0, agent.fatigue * FATIGUE_DECAY_MULT - FATIGUE_DECAY_SUB)


def grow_social_need(agent: AgentState) -> None:
    """Time-passing nudges social need upward, capped at 1.0."""

    agent.social_need = min(1.0, agent.social_need + SOCIAL_NEED_GROW)


# ---------------------------------------------------------------------------
# Post-action handlers (called from the tick loop AFTER the action commits)
# ---------------------------------------------------------------------------


def after_create_spot(agent: AgentState) -> None:
    """Hosting is the most tiring action but modest social payoff."""

    agent.fatigue = _clamp01(agent.fatigue + CREATE_FATIGUE_DELTA)
    agent.social_need = _clamp01(agent.social_need + CREATE_SOCIAL_DELTA)


def after_join_spot(agent: AgentState) -> None:
    """Joining is cheaper than hosting but satisfies social_need more."""

    agent.fatigue = _clamp01(agent.fatigue + JOIN_FATIGUE_DELTA)
    agent.social_need = _clamp01(agent.social_need + JOIN_SOCIAL_DELTA)


def after_complete_spot(agent: AgentState) -> None:
    """Completing a spot gives the biggest social_need drop (true fulfilment)."""

    agent.fatigue = _clamp01(agent.fatigue + COMPLETE_FATIGUE_DELTA)
    agent.social_need = _clamp01(agent.social_need + COMPLETE_SOCIAL_DELTA)
