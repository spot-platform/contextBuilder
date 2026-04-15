"""Tick -> (day_type, time_slot) mapping — plan §2.5.

Pure functions; no agent/spot state is touched here. The contract the
engine uses to look up `AgentState.schedule_weights` is:

    schedule_key(tick) == f"{day_type}_{time_slot}"

Any change to the key format must be mirrored in models/agent.py's
`schedule_weights` docstring.
"""

from __future__ import annotations

# Hour-range (inclusive on both ends) -> slot name.
# Plan §2.5 table, verbatim — do not reorder; iteration order is used below.
TIME_SLOTS: dict[tuple[int, int], str] = {
    (0, 6): "dawn",
    (7, 9): "morning",
    (10, 11): "late_morning",
    (12, 13): "lunch",
    (14, 17): "afternoon",
    (18, 20): "evening",
    (21, 23): "night",
}


def get_time_slot(tick: int) -> str:
    """Return the time-slot name for the hour implied by `tick`.

    Boundaries are inclusive on both ends (`start <= hour <= end`). Any hour
    that fails to match (shouldn't happen with the current TIME_SLOTS cover
    of 0..23 but is defensive) falls back to `"dawn"`.
    """

    hour = tick % 24
    for (start, end), slot in TIME_SLOTS.items():
        if start <= hour <= end:
            return slot
    return "dawn"


def get_day_type(tick: int) -> str:
    """Weekday/weekend classification based on `tick`.

    Day 0 is interpreted as a weekday (Monday). Days 5 and 6 are weekend.
    """

    day = (tick // 24) % 7
    return "weekend" if day >= 5 else "weekday"


def schedule_key(tick: int) -> str:
    """Return the composite key used to index `AgentState.schedule_weights`.

    This is the single source of truth for the key format; engine code must
    call this helper instead of concatenating strings inline.
    """

    return f"{get_day_type(tick)}_{get_time_slot(tick)}"
