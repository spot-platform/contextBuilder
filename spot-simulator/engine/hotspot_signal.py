"""Map-anchor and hotspot-signal payload helpers.

Phase-2 hotspots stay deterministic map signals. They intentionally do not
produce LLM-polished feed copy; AI feed generation remains a later validated
pipeline step.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
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

_TAGS_BY_VENUE = {
    "cafe": ("is_cafe", "is_group_friendly"),
    "home": ("is_cafe", "is_group_friendly"),
    "studio": ("is_lesson", "is_activity", "is_culture"),
    "park": ("is_park", "is_activity"),
    "gym": ("is_activity",),
    "online": ("is_cafe",),
}

# Skill-topic fallbacks keep this module independent from the synthetic-content
# package while still using the local-context-builder POI dump when available.
_TAGS_BY_SKILL_KEYWORD = {
    "요가": ("is_activity",),
    "볼더링": ("is_activity",),
    "러닝": ("is_park", "is_activity"),
    "등산": ("is_park", "is_activity"),
    "사진": ("is_culture", "is_park"),
    "공예": ("is_lesson", "is_culture"),
    "기타": ("is_lesson", "is_culture"),
    "일본어": ("is_cafe", "is_lesson"),
}


@lru_cache(maxsize=1)
def _poi_places() -> tuple[dict, ...]:
    """Load the normalized POI dump produced by local-context-builder.

    The simulator remains runnable without the dump: an empty tuple falls back to
    the privacy-safe region jitter anchor used by the previous implementation.
    """

    root = Path(__file__).resolve().parents[2]
    candidates = (
        root / "synthetic-content-pipeline" / "data" / "poi" / "poi_normalized_v1.json",
        root / "synthetic-content-pipeline" / "data" / "poi" / "poi_normalized_fallback.json",
    )
    for path in candidates:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            blob = json.load(fh)
        places = blob.get("places", []) if isinstance(blob, dict) else []
        return tuple(p for p in places if isinstance(p, dict))
    return ()


def _unit(seed: str, salt: str) -> float:
    digest = hashlib.sha256(f"{seed}:{salt}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0x100000000


def _jitter(center: float, seed: str, salt: str, radius: float = 0.004) -> float:
    return center + (_unit(seed, salt) - 0.5) * radius


def _stable_int(seed: str) -> int:
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12], 16)


def _float_feature(region: Mapping, key: str, default: float = 0.5) -> float:
    value = region.get(key, default)
    if value is None:
        return default
    return float(value)


def _skill_tags(skill_topic: str | None) -> tuple[str, ...]:
    skill = skill_topic or ""
    for keyword, tags in _TAGS_BY_SKILL_KEYWORD.items():
        if keyword in skill:
            return tags
    return ()


def _poi_matches_tags(poi: Mapping, tags: tuple[str, ...]) -> bool:
    return not tags or any(bool(poi.get(tag)) for tag in tags)


def _select_poi_anchor(
    *,
    spot: Spot,
    host: AgentState,
    region: Mapping,
) -> Mapping | None:
    region_name = str(region.get("region_name") or "")
    if not region_name:
        return None

    places = [p for p in _poi_places() if p.get("region_emd") == region_name]
    if not places:
        return None

    venue = (spot.venue_type or "cafe").lower()
    venue_tags = _TAGS_BY_VENUE.get(venue, ())
    skill_tags = _skill_tags(spot.skill_topic)
    preferred = [
        p for p in places
        if float(p.get("mapping_confidence", 1.0)) >= 0.7
        and (_poi_matches_tags(p, venue_tags) or _poi_matches_tags(p, skill_tags))
    ]
    if not preferred:
        preferred = [p for p in places if float(p.get("mapping_confidence", 1.0)) >= 0.7]
    if not preferred:
        return None

    seed = f"{spot.spot_id}:{spot.skill_topic}:{spot.venue_type}:{host.agent_id}:poi"
    return sorted(
        preferred,
        key=lambda p: (
            _stable_int(f"{seed}:{p.get('place_id')}"),
            str(p.get("place_id")),
        ),
    )[0]


def _region_jitter_anchor(
    *,
    spot: Spot,
    host: AgentState,
    region: Mapping,
) -> dict:
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


def build_map_anchor_payload(
    *,
    spot: Spot,
    host: AgentState,
    region_features: Mapping[str, Mapping],
) -> dict:
    """Build a deterministic public map anchor for map rendering.

    Prefer an actual local-context-builder POI in the spot region. If the POI
    dump or a suitable candidate is unavailable, retain the previous region
    center + jitter fallback so simulation runs stay reproducible offline.
    """

    region = region_features.get(spot.region_id, {}) if isinstance(region_features, Mapping) else {}
    venue = (spot.venue_type or "cafe").lower()
    home_safe = venue == "home"
    poi = _select_poi_anchor(spot=spot, host=host, region=region)
    if poi is None:
        return _region_jitter_anchor(spot=spot, host=host, region=region)

    return {
        "type": "poi_public_anchor" if not home_safe else "home_public_proxy_poi",
        "lat": round(float(poi["lat"]), 7),
        "lng": round(float(poi["lng"]), 7),
        "region_id": spot.region_id,
        "category": _CATEGORY_BY_VENUE.get(venue, "public_meetup_proxy"),
        "confidence": round(float(poi.get("mapping_confidence", 1.0)), 2),
        "match_reason": "lcb_poi_match" if not home_safe else "home_venue_public_poi_proxy",
        "poi_id": str(poi.get("place_id")),
        "poi_name": str(poi.get("name", "")),
        "poi_category": str(poi.get("primary_category", "")),
        "address": poi.get("road_address_name") or poi.get("address_name"),
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
            "density_cafe": _float_feature(region, "density_cafe"),
            "density_food": _float_feature(region, "density_food"),
            "density_nature": _float_feature(region, "density_nature"),
            "night_friendliness": _float_feature(region, "night_friendliness"),
            "group_friendliness": _float_feature(region, "group_friendliness"),
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
