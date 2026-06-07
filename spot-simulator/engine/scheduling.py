"""Temporal variance helpers for peer-mode simulation logs.

The simulator owns timing now: FE should consume `scheduled_tick`,
`duration_ticks`, and `expected_closed_at_tick` instead of inventing lifetimes.
All helpers are deterministic for the injected run RNG.
"""

from __future__ import annotations

import hashlib
import random
from typing import Mapping

from models.agent import AgentState


def _as_pair(value, default: tuple[int, int]) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = int(value[0]), int(value[1])
    elif isinstance(value, Mapping):
        lo, hi = int(value.get("min", default[0])), int(value.get("max", default[1]))
    else:
        lo, hi = default
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def _stable_mod(key: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


def temporal_config(config: Mapping | None) -> Mapping:
    """Return the peer temporal variance block with safe defaults."""

    if not isinstance(config, Mapping):
        return {}
    peer = config.get("peer", {}) if isinstance(config.get("peer", {}), Mapping) else {}
    tv = peer.get("temporal_variance", {}) if isinstance(peer.get("temporal_variance", {}), Mapping) else {}
    return tv


def get_persona_rhythm_offset(agent: AgentState, cfg: Mapping | None = None) -> int:
    """Small deterministic per-agent offset that prevents synchronized actions."""

    cfg = cfg or {}
    ranges = cfg.get("persona_rhythm_offsets", {}) if isinstance(cfg, Mapping) else {}
    persona_type = str(getattr(agent, "persona_type", "default") or "default")

    # Keyword-based buckets keep this independent from exact persona yaml names.
    if "night" in persona_type:
        bucket = "night"
    elif "morning" in persona_type:
        bucket = "morning"
    elif "home" in persona_type:
        bucket = "homebody"
    elif "social" in persona_type:
        bucket = "social"
    else:
        bucket = "default"

    lo, hi = _as_pair(ranges.get(bucket), _as_pair(ranges.get("default"), (0, 5)))
    return lo + _stable_mod(f"{agent.agent_id}:{persona_type}:rhythm", hi - lo + 1)


def pick_scheduled_tick(
    *,
    current_tick: int,
    host: AgentState,
    skill: str,
    teach_mode: str,
    venue_type: str,
    origination_mode: str,
    rng: random.Random,
    config: Mapping | None = None,
) -> int:
    """Pick a future scheduled tick with persona/venue/mode variance."""

    cfg = temporal_config(config)
    lead_cfg = cfg.get("schedule_lead_ticks", {}) if isinstance(cfg, Mapping) else {}
    default = (4, 30) if origination_mode == "request_matched" else (6, 36)
    lo, hi = _as_pair(lead_cfg.get(origination_mode), default)
    lead = rng.randint(lo, hi)

    # Persona rhythm is deliberately small; it spreads clusters without making
    # sessions absurdly far in the future.
    lead += get_persona_rhythm_offset(host, cfg) // 2

    mode_mod = {"1:1": -2, "small_group": 2, "workshop": 8}.get(teach_mode, 0)
    venue_mod = {
        "home": 2,
        "cafe": 0,
        "park": 1,
        "studio": 4,
        "gym": 4,
        "online": -1,
    }.get(venue_type, 0)
    skill_mod = _stable_mod(str(skill), 3) - 1
    lead = max(1, lead + mode_mod + venue_mod + skill_mod)
    return current_tick + lead


def pick_spot_duration(
    *,
    skill: str,
    teach_mode: str,
    venue_type: str,
    rng: random.Random,
    config: Mapping | None = None,
) -> int:
    """Pick duration ticks by teach mode and venue."""

    cfg = temporal_config(config)
    duration_cfg = cfg.get("duration_ticks", {}) if isinstance(cfg, Mapping) else {}
    defaults = {"1:1": (1, 2), "small_group": (2, 4), "workshop": (3, 6)}
    lo, hi = _as_pair(duration_cfg.get(teach_mode), defaults.get(teach_mode, (2, 4)))
    duration = rng.randint(lo, hi)
    duration += {
        "studio": 1,
        "gym": 1,
        "home": 1,
        "park": _stable_mod(str(skill), 2),
        "online": -1,
    }.get(venue_type, 0)
    return max(1, duration)
