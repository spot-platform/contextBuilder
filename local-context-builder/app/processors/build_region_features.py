"""Plan §7 STEP 4 — region-level feature vectors.

For every active region inside ``target_city`` we compute:

- 5 densities (food, cafe, activity, nightlife, lesson)
- 3 access/quality scores (park, culture, night liveliness)
- 4 spot-suitability scores (casual_meetup, lesson_spot,
  solo_activity, group_activity)

**percentile_rank must be applied to the whole-city array at once** —
calling it per region would always return 0 since a single-value array
has no relative ordering. Density raw values live on the row as well
so downstream QA can inspect them.

Blending fields (``kakao_raw_score``, ``real_data_score``,
``blended_score``, ``alpha_used``, ``beta_used``) are populated by the
MVP too: alpha=1, beta=0 since STEP 5 (real data) is a v1.1 stub.

The upsert key is ``(region_id, dataset_version)`` — re-running a
build for the same version overwrites the row, but a new version
always inserts a fresh one (append-only per plan §4-5).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.place_normalized import PlaceNormalized
from app.models.region import RegionMaster
from app.models.region_feature import RegionFeature
from app.services.scoring_service import (
    clip01,
    percentile_rank,
    sigmoid_normalize,
    weighted_avg,
)

logger = logging.getLogger(__name__)

# Density / count aggregation per region.
_DENSITY_KEYS = ("food", "cafe", "activity", "nightlife", "lesson")
_COUNT_KEYS = ("park", "culture")


def _load_regions(db, target_city: str) -> list[RegionMaster]:
    stmt = (
        select(RegionMaster)
        .where(RegionMaster.is_active.is_(True))
        .where(RegionMaster.target_city == target_city)
        .order_by(RegionMaster.id)
    )
    return list(db.scalars(stmt).all())


def _load_places_by_region(
    db, region_ids: list[int]
) -> dict[int, list[PlaceNormalized]]:
    if not region_ids:
        return {}
    stmt = select(PlaceNormalized).where(
        PlaceNormalized.region_id.in_(region_ids)
    )
    grouped: dict[int, list[PlaceNormalized]] = {rid: [] for rid in region_ids}
    for row in db.scalars(stmt).all():
        grouped.setdefault(row.region_id, []).append(row)
    return grouped


def _count_raw_places(db, region_ids: list[int]) -> dict[int, int]:
    """Collector table row count per region (plan says feature must
    include both raw_place_count and normalized_place_count)."""

    if not region_ids:
        return {}
    # Avoid importing PlaceRawKakao at module import to keep the import
    # surface small. It's already wired through app.models.
    from app.models.place_raw import PlaceRawKakao
    from sqlalchemy import func

    stmt = (
        select(PlaceRawKakao.region_id, func.count(PlaceRawKakao.id))
        .where(PlaceRawKakao.region_id.in_(region_ids))
        .group_by(PlaceRawKakao.region_id)
    )
    return {row[0]: int(row[1]) for row in db.execute(stmt).all()}


def _compute_counts(
    places: list[PlaceNormalized],
) -> tuple[dict[str, int], dict[str, int]]:
    density_counts = {k: 0 for k in _DENSITY_KEYS}
    other_counts = {k: 0 for k in _COUNT_KEYS}
    for place in places:
        for key in _DENSITY_KEYS:
            if getattr(place, f"is_{key}", False):
                density_counts[key] += 1
        for key in _COUNT_KEYS:
            if getattr(place, f"is_{key}", False):
                other_counts[key] += 1
    return density_counts, other_counts


def _density(count: int, area_km2: float | None) -> float:
    if area_km2 is None or area_km2 <= 0:
        return 0.0
    return count / area_km2


def build(db, dataset_version: str, target_city: str) -> dict[str, int]:
    """Build one dataset_version worth of region_feature rows.

    Returns ``{"regions": N, "upserted": N}``.
    """

    regions = _load_regions(db, target_city)
    if not regions:
        logger.warning(
            "build_region_features: no active regions for target_city=%s",
            target_city,
        )
        return {"regions": 0, "upserted": 0}

    region_ids = [r.id for r in regions]
    places_by_region = _load_places_by_region(db, region_ids)
    raw_counts_by_region = _count_raw_places(db, region_ids)

    # Step 1: per-region raw counts + densities.
    per_region: list[dict[str, Any]] = []
    for region in regions:
        places = places_by_region.get(region.id, [])
        density_counts, other_counts = _compute_counts(places)

        row: dict[str, Any] = {
            "region": region,
            "normalized_place_count": len(places),
            "raw_place_count": raw_counts_by_region.get(region.id, 0),
            "density_counts": density_counts,
            "other_counts": other_counts,
        }
        for key in _DENSITY_KEYS:
            row[f"{key}_density"] = _density(density_counts[key], region.area_km2)
        per_region.append(row)

    # Step 2: city-wide percentile_rank per density key.
    density_norms: dict[str, list[float]] = {}
    for key in _DENSITY_KEYS:
        arr = [float(row[f"{key}_density"]) for row in per_region]
        density_norms[key] = percentile_rank(arr)

    # Step 3: per-region derived scores.
    upserted = 0
    for idx, row in enumerate(per_region):
        region: RegionMaster = row["region"]
        food_norm = clip01(density_norms["food"][idx])
        cafe_norm = clip01(density_norms["cafe"][idx])
        activity_norm = clip01(density_norms["activity"][idx])
        nightlife_norm = clip01(density_norms["nightlife"][idx])
        lesson_norm = clip01(density_norms["lesson"][idx])

        park_count = row["other_counts"]["park"]
        culture_count = row["other_counts"]["culture"]
        park_access = clip01(min(1.0, park_count / 3.0))
        culture_score = clip01(min(1.0, culture_count / 5.0))
        night_liveliness = clip01(
            sigmoid_normalize(
                row["nightlife_density"], midpoint=0.5, steepness=4.0
            )
        )

        # Plan §7 weighted spot-suitability scores.
        casual_meetup = clip01(
            weighted_avg(
                [
                    (food_norm, 0.40),
                    (cafe_norm, 0.35),
                    (park_access, 0.25),
                ]
            )
        )
        lesson_spot = clip01(
            weighted_avg(
                [
                    (lesson_norm, 0.50),
                    (culture_score, 0.30),
                    (activity_norm, 0.20),
                ]
            )
        )
        solo_activity = clip01(
            weighted_avg(
                [
                    (cafe_norm, 0.40),
                    (park_access, 0.30),
                    (culture_score, 0.30),
                ]
            )
        )
        group_activity = clip01(
            weighted_avg(
                [
                    (activity_norm, 0.40),
                    (food_norm, 0.35),
                    (lesson_norm, 0.25),
                ]
            )
        )

        # kakao_raw_score is a simple average of the suitability scores
        # so downstream blending always has something to compare against.
        kakao_raw_score = clip01(
            weighted_avg(
                [
                    (casual_meetup, 1.0),
                    (lesson_spot, 1.0),
                    (solo_activity, 1.0),
                    (group_activity, 1.0),
                ]
            )
        )
        # MVP: no real data → alpha=1, beta=0 natural fallback (plan §8-6).
        alpha_used = 1.0
        beta_used = 0.0
        real_data_score = 0.0
        blended_score = clip01(
            alpha_used * kakao_raw_score + beta_used * real_data_score
        )

        feature_json = {
            "density_raw": {
                key: row[f"{key}_density"] for key in _DENSITY_KEYS
            },
            "density_norm": {
                "food": food_norm,
                "cafe": cafe_norm,
                "activity": activity_norm,
                "nightlife": nightlife_norm,
                "lesson": lesson_norm,
            },
            "park_count": park_count,
            "culture_count": culture_count,
        }

        values: dict[str, Any] = {
            "region_id": region.id,
            "dataset_version": dataset_version,
            "food_density": row["food_density"],
            "cafe_density": row["cafe_density"],
            "activity_density": row["activity_density"],
            "nightlife_density": row["nightlife_density"],
            "lesson_density": row["lesson_density"],
            "park_access_score": park_access,
            "culture_score": culture_score,
            "night_liveliness_score": night_liveliness,
            "casual_meetup_score": casual_meetup,
            "lesson_spot_score": lesson_spot,
            "solo_activity_score": solo_activity,
            "group_activity_score": group_activity,
            "kakao_raw_score": kakao_raw_score,
            "real_data_score": real_data_score,
            "blended_score": blended_score,
            "alpha_used": alpha_used,
            "beta_used": beta_used,
            "raw_place_count": row["raw_place_count"],
            "normalized_place_count": row["normalized_place_count"],
            "feature_json": feature_json,
        }

        stmt = pg_insert(RegionFeature).values(**values)
        update_cols = {
            col: stmt.excluded[col]
            for col in values.keys()
            if col not in {"region_id", "dataset_version"}
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_region_feature_region_version",
            set_=update_cols,
        )
        db.execute(stmt)
        upserted += 1

    db.commit()
    logger.info(
        "build_region_features: dataset_version=%s regions=%d upserted=%d",
        dataset_version,
        len(regions),
        upserted,
    )
    return {"regions": len(regions), "upserted": upserted}
