"""Spot lifecycle processor — plan §3.3 / §3.4 (Phase 2).

`process_lifecycle` advances spot state machines one step per tick:

    OPEN        -> CANCELED     (48h timeout without MATCHED)
    MATCHED     -> CONFIRMED    (scheduled_tick - tick <= 2)
    CONFIRMED   -> IN_PROGRESS  (tick >= scheduled_tick)
    IN_PROGRESS -> COMPLETED    (tick >= scheduled_tick + duration, noshow<=50%)
    IN_PROGRESS -> DISPUTED     (tick >= scheduled_tick + duration, noshow>50%)

Single-pass per tick — a spot transitions at most once per invocation,
which keeps rng draws / event emission order stable and deterministic.

All mutations happen in place. Check-in / no-show marking for the
CONFIRMED->IN_PROGRESS step is NOT done here; runner.py runs a dedicated
"check-in pass" over agents right after the lifecycle pass so the random
draws stay ordered by shuffled agent iteration, not spot iteration.
"""

from __future__ import annotations

import random

from engine.decay import after_complete_spot
from models import AgentState, EventLog, Spot, SpotStatus, make_event

# ---------------------------------------------------------------------------
# Tunable constants (plan §3.4)
# ---------------------------------------------------------------------------

#: An OPEN spot older than this (ticks) is force-canceled as a SPOT_TIMEOUT.
#: Lowered from 48 -> 24 so the CANCELED ratio lands in the 15-30% QA
#: target band (plan §3.3). Phase 1 never invokes the lifecycle processor,
#: so this retune has no effect on Phase 1 determinism.
OPEN_TIMEOUT_TICKS: int = 40

#: A MATCHED spot flips to CONFIRMED once its scheduled start is within this
#: many ticks. Plan §3.4 says "2 hours before start".
CONFIRM_LEAD_TICKS: int = 2

#: Fraction of participants that must be NO_SHOW to push a completing spot
#: into DISPUTED instead of COMPLETED.
NOSHOW_DISPUTE_THRESHOLD: float = 0.5


def process_lifecycle(
    spots: list[Spot],
    tick: int,
    event_log: list[EventLog],
    agents_by_id: dict[str, AgentState],
    *,
    rng: random.Random,
) -> None:
    """Advance every spot's lifecycle one step and append events in place.

    Runs BEFORE the agent decision pass each tick. The `rng` is accepted for
    forward compatibility (Phase 2 lifecycle itself is deterministic — no
    random draws happen here — but Phase 3 dispute-resolution will need it
    and we don't want to churn the signature again).
    """
    del rng  # unused in Phase 2 lifecycle; kept for forward compatibility

    for spot in spots:
        # --- OPEN -> CANCELED (timeout) ----------------------------------
        if spot.status == SpotStatus.OPEN:
            if tick - spot.created_at_tick > OPEN_TIMEOUT_TICKS:
                spot.status = SpotStatus.CANCELED
                spot.canceled_at_tick = tick
                event_log.append(
                    make_event(tick, "SPOT_TIMEOUT", agent=None, spot=spot)
                )
            continue

        # --- MATCHED -> CONFIRMED (lead-time gate) -----------------------
        if spot.status == SpotStatus.MATCHED:
            if spot.scheduled_tick - tick <= CONFIRM_LEAD_TICKS:
                spot.status = SpotStatus.CONFIRMED
                spot.confirmed_at_tick = tick
                event_log.append(
                    make_event(tick, "SPOT_CONFIRMED", agent=None, spot=spot)
                )
                # Pin host + participants: they now "owe" this spot a check-in.
                host = agents_by_id.get(spot.host_agent_id)
                if host is not None and spot.spot_id not in host.confirmed_spots:
                    host.confirmed_spots.append(spot.spot_id)
                for pid in spot.participants:
                    agent = agents_by_id.get(pid)
                    if agent is None:
                        continue
                    if spot.spot_id not in agent.confirmed_spots:
                        agent.confirmed_spots.append(spot.spot_id)
            continue

        # --- CONFIRMED -> IN_PROGRESS (scheduled tick reached) -----------
        if spot.status == SpotStatus.CONFIRMED:
            if tick >= spot.scheduled_tick:
                spot.status = SpotStatus.IN_PROGRESS
                spot.started_at_tick = tick
                event_log.append(
                    make_event(tick, "SPOT_STARTED", agent=None, spot=spot)
                )
            continue

        # --- IN_PROGRESS -> COMPLETED / DISPUTED (duration elapsed) ------
        if spot.status == SpotStatus.IN_PROGRESS:
            if tick >= spot.scheduled_tick + spot.duration:
                total = len(spot.participants)
                if total > 0:
                    noshow_ratio = len(spot.noshow) / total
                else:
                    noshow_ratio = 0.0

                if total > 0 and noshow_ratio > NOSHOW_DISPUTE_THRESHOLD:
                    spot.status = SpotStatus.DISPUTED
                    spot.disputed_at_tick = tick
                    event_log.append(
                        make_event(tick, "SPOT_DISPUTED", agent=None, spot=spot)
                    )
                else:
                    spot.status = SpotStatus.COMPLETED
                    spot.completed_at_tick = tick
                    event_log.append(
                        make_event(tick, "SPOT_COMPLETED", agent=None, spot=spot)
                    )
                    for pid in spot.checked_in:
                        agent = agents_by_id.get(pid)
                        if agent is not None:
                            after_complete_spot(agent)
            continue
