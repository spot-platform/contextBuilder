"""Persona return-home events after spot completion.

The frontend should not snap everyone home on `SPOT_COMPLETED`. This module
turns completion into staggered LEAVE/RETURN events owned by the simulator.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Mapping

from engine.scheduling import temporal_config
from models.agent import AgentState
from models.event import EventLog, make_event
from models.spot import Spot


@dataclass
class PendingReturn:
    persona_id: str
    spot_id: str
    from_region_id: str
    to_region_id: str
    leave_tick: int
    return_home_tick: int
    leave_emitted: bool = False


def _pair(value, default: tuple[int, int]) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = int(value[0]), int(value[1])
    else:
        lo, hi = default
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def _persona_bucket(agent: AgentState) -> str:
    persona_type = str(getattr(agent, "persona_type", "") or "").lower()
    if "night" in persona_type:
        return "night_social"
    if "home" in persona_type:
        return "homebody"
    if "social" in persona_type:
        return "social"
    return "default"


def _range_from_cfg(cfg: Mapping, key: str, subkey: str, default: tuple[int, int]) -> tuple[int, int]:
    group = cfg.get(key, {}) if isinstance(cfg.get(key, {}), Mapping) else {}
    return _pair(group.get(subkey), default)


def schedule_returns_for_completed_spot(
    *,
    spot: Spot,
    agents_by_id: Mapping[str, AgentState],
    tick: int,
    rng: random.Random,
    config: Mapping | None = None,
) -> list[PendingReturn]:
    """Create staggered return-home records for host + arrived participants."""

    tv = temporal_config(config)
    return_cfg = tv.get("return_home", {}) if isinstance(tv.get("return_home", {}), Mapping) else {}

    roster: list[str] = []
    if spot.host_agent_id not in roster:
        roster.append(spot.host_agent_id)
    for pid in sorted(spot.checked_in):
        if pid not in roster:
            roster.append(pid)

    pending: list[PendingReturn] = []
    for pid in roster:
        agent = agents_by_id.get(pid)
        if agent is None:
            continue
        bucket = _persona_bucket(agent)
        linger_ranges = return_cfg.get("linger_ticks_by_persona", {}) if isinstance(return_cfg.get("linger_ticks_by_persona", {}), Mapping) else {}
        linger_lo, linger_hi = _pair(linger_ranges.get(bucket), _pair(linger_ranges.get("default"), (0, 3)))
        linger = rng.randint(linger_lo, linger_hi)

        travel_ranges = return_cfg.get("travel_ticks", {}) if isinstance(return_cfg.get("travel_ticks", {}), Mapping) else {}
        travel_key = "same_region" if agent.home_region_id == spot.region_id else "nearby_region"
        travel_lo, travel_hi = _pair(travel_ranges.get(travel_key), (0, 1) if travel_key == "same_region" else (1, 2))
        travel = rng.randint(travel_lo, travel_hi)

        leave_tick = tick + linger
        return_tick = leave_tick + travel
        pending.append(
            PendingReturn(
                persona_id=pid,
                spot_id=spot.spot_id,
                from_region_id=spot.region_id,
                to_region_id=agent.home_region_id,
                leave_tick=leave_tick,
                return_home_tick=return_tick,
            )
        )
    return pending


def process_pending_returns(pending: list[PendingReturn], tick: int) -> list[EventLog]:
    events: list[EventLog] = []
    remaining: list[PendingReturn] = []
    for item in pending:
        if not item.leave_emitted and tick >= item.leave_tick:
            item.leave_emitted = True
            events.append(
                make_event(
                    tick=tick,
                    event_type="PERSONA_LEAVE_SPOT",
                    payload={
                        "persona_id": item.persona_id,
                        "spot_id": item.spot_id,
                        "from_region_id": item.from_region_id,
                        "to_region_id": item.to_region_id,
                        "leave_tick": tick,
                        "return_home_tick": item.return_home_tick,
                        "reason": "activity_completed",
                    },
                )
            )
        if tick >= item.return_home_tick:
            events.append(
                make_event(
                    tick=tick,
                    event_type="PERSONA_RETURN_HOME",
                    payload={
                        "persona_id": item.persona_id,
                        "spot_id": item.spot_id,
                        "from_region_id": item.from_region_id,
                        "to_region_id": item.to_region_id,
                        "returned_at_tick": tick,
                        "reason": "activity_completed",
                    },
                )
            )
        else:
            remaining.append(item)
    pending[:] = remaining
    return events
