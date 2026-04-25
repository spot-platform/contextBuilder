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


# ---------------------------------------------------------------------------
# Tick <-> virtual wall-clock (ms) conversion — FE handoff 2026-04-24
# ---------------------------------------------------------------------------
#
# FE handoff (`BACKEND_HANDOFF_ENTITIES.md §SpotLifecycle`) requires all
# timestamps on the SpotLifecycle stream (`created_at_ms`, `matched_at_ms`,
# `joined_at_ms`, `closed_at_ms`, `expected_closed_at_ms`, `arrived_at_ms`)
# to be **simulation virtual-time milliseconds** — not wall clock. The BE
# server reads `event_log.jsonl` and converts `tick` → `ms` via these helpers
# before publishing SSE frames.
#
# `TICK_DURATION_MS` is derived from `simulation_config.yaml::*.time_resolution_hours`.
# Current configs hold it at 1h (phase_1/2/3), so one tick == one hour ==
# 3_600_000ms. If the resolution ever changes, `make_run_clock()` reads the
# config value and returns a closure bound to that run.

TICK_DURATION_MS_PER_HOUR: int = 3_600_000


def tick_to_virtual_ms(
    tick: int,
    *,
    run_start_ms: int = 0,
    time_resolution_hours: int = 1,
) -> int:
    """Convert a `tick` into virtual-time milliseconds for FE consumption.

    `run_start_ms` anchors tick=0. A run is deterministic by `seed`, so
    `run_start_ms` is typically taken from `SimulationRun.started_at` at the
    moment the BE server serializes the run. Simulator code that cannot see
    `run_start_ms` (it is not in `simulation_config.yaml`) should pass 0 —
    the BE publisher adds the offset when turning ticks into wall clock.

    Guarantees:
      - monotonically non-decreasing with `tick`
      - byte-identical output for equal `(tick, run_start_ms, time_resolution_hours)`
      - safe for `tick < 0` (negative offsets are valid; used for
        `wait_deadline_tick = -1` sentinels)
    """

    return run_start_ms + tick * time_resolution_hours * TICK_DURATION_MS_PER_HOUR


def make_run_clock(
    *,
    run_start_ms: int = 0,
    time_resolution_hours: int = 1,
):
    """Return a closure that converts `tick` → virtual ms.

    Useful when the engine emits many events and does not want to pass
    `run_start_ms` / `time_resolution_hours` to every `payload` call site.
    Usage:

        clock = make_run_clock(run_start_ms=0, time_resolution_hours=1)
        payload = {"joined_at_ms": clock(tick), ...}
    """

    def _clock(tick: int) -> int:
        return tick_to_virtual_ms(
            tick,
            run_start_ms=run_start_ms,
            time_resolution_hours=time_resolution_hours,
        )

    return _clock
