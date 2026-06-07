"""Map-anchor and hotspot-signal payload helpers.

Phase-2 hotspots stay deterministic map signals. They intentionally do not
produce LLM-polished feed copy; AI feed generation remains a later validated
pipeline step.
"""

from __future__ import annotations

import hashlib
from typing import Mapping

from models.agent import AgentState
from models.spot import Spot


_CATEGORY_BY_VENUE = {
    "cafe": "cafe",
    "home": "public_meetup_proxy",
    "studio": "studio",
    "park": "park",
    "gym": "gym",
    "online": "online",
}


def _unit(seed: str, salt: str) -> float:
    digest = hashlib.sha256(f"{seed}:{salt}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _jitter(center: float, seed: str, salt: str, radius: float = 0.004) -> float:
    return center + (_unit(seed, salt) - 0.5) * radius


def build_map_anchor_payload(
    *,
    spot: Spot,
    host: AgentState,
    region_features: Mapping[str, Mapping],
) -> dict:
    """Build a public-safe deterministic anchor for map rendering.

    This first version uses region public jitter. It is shaped so a future POI
    resolver can swap `type=poi` without changing FE/BE contracts.
    """

    region = region_features.get(spot.region_id, {}) if isinstance(region_features, Mapping) else {}
    lat = float(region.get("center_lat", 37.27))
    lng = float(region.get("center_lng", 127.03))
    seed = f"{spot.spot_id}:{spot.skill_topic}:{spot.venue_type}:{host.agent_id}"
    venue = spot.venue_type or "cafe"
    home_safe = venue == "home"
    return {
        "type": "region_public_jitter" if not home_safe else "home_public_proxy_jitter",
        "lat": round(_jitter(lat, seed, "lat"), 7),
        "lng": round(_jitter(lng, seed, "lng"), 7),
        "region_id": spot.region_id,
        "category": _CATEGORY_BY_VENUE.get(venue, "public_meetup_proxy"),
        "confidence": 0.55 if not home_safe else 0.45,
        "match_reason": "region_center_public_jitter" if not home_safe else "home_venue_public_proxy",
    }


def build_hotspot_signal_payload(
    *,
    spot: Spot,
    host: AgentState,
    region_features: Mapping[str, Mapping],
) -> dict:
    region = region_features.get(spot.region_id, {}) if isinstance(region_features, Mapping) else {}
    reason_tags: list[str] = []
    if host.home_region_id == spot.region_id:
        reason_tags.append("same_region_host")
    if spot.teach_mode:
        reason_tags.append(spot.teach_mode)
    if spot.venue_type:
        reason_tags.append(f"venue:{spot.venue_type}")
    if float(region.get("density_cafe", 0.0)) >= 0.7:
        reason_tags.append("cafe_dense_region")
    if float(region.get("group_friendliness", 0.0)) >= 0.7:
        reason_tags.append("group_friendly_region")
    if float(region.get("night_friendliness", 0.0)) >= 0.7:
        reason_tags.append("night_friendly_region")

    return {
        "signal_type": "teach_spot",
        "state": "forming",
        "host_persona_type": host.persona_type,
        "participant_target_count": spot.capacity,
        "reason_tags": reason_tags,
        "region_character": {
            "density_cafe": region.get("density_cafe"),
            "density_food": region.get("density_food"),
            "density_nature": region.get("density_nature"),
            "night_friendliness": region.get("night_friendliness"),
            "group_friendliness": region.get("group_friendliness"),
        },
    }


def build_create_spot_signal_payload(
    *,
    spot: Spot,
    host: AgentState,
    region_features: Mapping[str, Mapping],
) -> dict:
    return {
        "map_anchor": build_map_anchor_payload(
            spot=spot, host=host, region_features=region_features
        ),
        "hotspot_signal": build_hotspot_signal_payload(
            spot=spot, host=host, region_features=region_features
        ),
    }
