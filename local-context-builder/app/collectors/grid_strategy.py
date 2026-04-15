"""Adaptive grid splitting for dense regions.

Plan §5-3 requires probe-then-split behaviour: try one cell at 1km, and
if the probe returns 40+ results we split the region's bbox into a 2×2
grid. MVP (§15) caps the recursion at a single split level — real 3-step
recursion is a v1.1 concern.

The public surface is deliberately pure so tests can drive the function
with synthetic ``RegionMaster`` rows without any DB or HTTP at all.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.collectors.kakao_local_client import KakaoLocalClient
    from app.models.region import RegionMaster

logger = logging.getLogger(__name__)

# Plan §5-3: a 40+ probe flags a "dense" region.
DENSE_THRESHOLD = 40

# Default probe radius in metres. Matches the plan's 1km starting point.
DEFAULT_RADIUS_M = 1000

# Cap cell radius so we never exceed Kakao's 20km category-search limit.
MAX_RADIUS_M = 20_000

# The probe always uses FD6 (restaurants) because it is the densest
# category in virtually every Korean 행정동. If FD6 is not dense, the
# rest won't be either.
PROBE_CATEGORY = "FD6"

Cell = tuple[float, float, int]  # (center_lng, center_lat, radius_m)


def plan_cells(
    region: "RegionMaster",
    probe_count: int | None = None,
    *,
    threshold: int = DENSE_THRESHOLD,
) -> list[Cell]:
    """Return grid cells to sweep for ``region``.

    ``probe_count`` is the number of documents returned by a 1km probe.
    When ``None`` the caller has not probed yet, so we return the default
    single-cell plan. When ``probe_count >= threshold`` we split the
    region's bbox into a 2×2 grid. If the region has no bbox we cannot
    split and fall back to the default plan with a warning.
    """

    default: list[Cell] = [
        (float(region.center_lng), float(region.center_lat), DEFAULT_RADIUS_M)
    ]

    if probe_count is None or probe_count < threshold:
        return default

    if not _has_bbox(region):
        logger.warning(
            "region %s is dense (probe=%d) but has no bbox; using default cell",
            getattr(region, "region_code", "<unknown>"),
            probe_count,
        )
        return default

    return _split_bbox_2x2(region)


def probe_and_plan(
    region: "RegionMaster", client: "KakaoLocalClient"
) -> list[Cell]:
    """Run a single FD6 probe and feed the count into :func:`plan_cells`."""

    try:
        probe_docs = client.search_category(
            PROBE_CATEGORY,
            x=float(region.center_lng),
            y=float(region.center_lat),
            radius=DEFAULT_RADIUS_M,
        )
    except Exception as exc:  # noqa: BLE001 - probe is best-effort
        logger.warning(
            "probe failed for region %s: %s; falling back to default cell",
            getattr(region, "region_code", "<unknown>"),
            exc,
        )
        return plan_cells(region, probe_count=None)

    return plan_cells(region, probe_count=len(probe_docs))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _has_bbox(region: "RegionMaster") -> bool:
    return all(
        getattr(region, attr, None) is not None
        for attr in ("bbox_min_lng", "bbox_min_lat", "bbox_max_lng", "bbox_max_lat")
    )


def _split_bbox_2x2(region: "RegionMaster") -> list[Cell]:
    """Divide ``region.bbox_*`` into 4 equal sub-rectangles.

    Each resulting cell uses its centre as the search origin and a
    radius equal to *half of its diagonal* (so the whole sub-rectangle
    stays inside the probe circle). Radius is clamped to
    :data:`MAX_RADIUS_M`.
    """

    min_lng = float(region.bbox_min_lng)
    min_lat = float(region.bbox_min_lat)
    max_lng = float(region.bbox_max_lng)
    max_lat = float(region.bbox_max_lat)

    mid_lng = (min_lng + max_lng) / 2.0
    mid_lat = (min_lat + max_lat) / 2.0

    # Each quadrant's half-extents.
    half_lng = (max_lng - min_lng) / 4.0
    half_lat = (max_lat - min_lat) / 4.0

    centres = [
        (min_lng + half_lng, min_lat + half_lat),  # SW
        (max_lng - half_lng, min_lat + half_lat),  # SE
        (min_lng + half_lng, max_lat - half_lat),  # NW
        (max_lng - half_lng, max_lat - half_lat),  # NE
    ]

    # Approximate half-diagonal in metres. 1 deg lat ~= 111_320 m; 1 deg
    # lng scales by cos(lat). At quadrant scale we use the mid latitude.
    lat_rad = math.radians(mid_lat)
    half_width_m = half_lng * 111_320.0 * math.cos(lat_rad)
    half_height_m = half_lat * 111_320.0
    half_diag_m = math.hypot(half_width_m, half_height_m)

    # Pad a bit so circles overlap at the seams and we don't miss the
    # boundary. 1.2x is plenty for any realistic 행정동 quadrant.
    radius = int(min(max(half_diag_m * 1.2, 100), MAX_RADIUS_M))

    return [(lng, lat, radius) for lng, lat in centres]
