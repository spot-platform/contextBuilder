"""Plan §10 STEP 9 — spot_seed_dataset builder.

For every active region in ``target_city`` we expand the region_feature
into ``spot_type × category`` rows. ``final_weight`` is always clamped
to ``[0, 1]`` because the publish quality gate refuses values outside
that range.

``SPOT_TYPES`` maps a spot_type to the internal category tags it can
legitimately pull supply from. The demand/supply mix is computed from
the density-norm vector that ``build_region_features`` stored in
``feature_json``.

MVP provides fixed defaults for ``recommended_capacity``,
``recommended_time_slots`` and ``price_band``. v1.1 can replace these
with per-region inference once ``real_activity_agg`` is populated.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.region import RegionMaster
from app.models.region_feature import RegionFeature
from app.models.spot_seed import SpotSeedDataset
from app.services.scoring_service import clip01, weighted_avg

logger = logging.getLogger(__name__)

SPOT_TYPES: dict[str, list[str]] = {
    "casual_meetup": ["food", "cafe"],
    "lesson": ["lesson", "culture"],
    "activity": ["activity", "park"],
    "night_social": ["nightlife", "food"],
    "solo_healing": ["cafe", "park", "culture"],
}

# MVP fixed defaults (plan §15). v1.1 replaces these with inferred values.
_DEFAULT_CAPACITY: dict[str, int] = {
    "casual_meetup": 4,
    "lesson": 6,
    "activity": 6,
    "night_social": 6,
    "solo_healing": 3,
}
_DEFAULT_TIME_SLOTS: dict[str, list[str]] = {
    "casual_meetup": ["SAT_12", "SAT_18", "SUN_12"],
    "lesson": ["SAT_14", "SAT_16", "SUN_14"],
    "activity": ["SAT_10", "SAT_14", "SUN_10"],
    "night_social": ["FRI_19", "FRI_21", "SAT_19"],
    "solo_healing": ["SAT_10", "SUN_10", "WED_14"],
}
_DEFAULT_PRICE_BAND: dict[str, str] = {
    "casual_meetup": "mid",
    "lesson": "mid",
    "activity": "mid",
    "night_social": "high",
    "solo_healing": "low",
}


def _score_from_feature(
    feature: RegionFeature, category: str
) -> float:
    """Return a normalized [0,1] supply proxy for a category tag."""

    norms = (feature.feature_json or {}).get("density_norm", {}) or {}
    if category in norms:
        return clip01(float(norms.get(category, 0.0) or 0.0))
    if category == "park":
        return clip01(float(feature.park_access_score or 0.0))
    if category == "culture":
        return clip01(float(feature.culture_score or 0.0))
    return 0.0


def _spot_type_suitability(
    feature: RegionFeature, spot_type: str
) -> float:
    """Read the pre-computed spot suitability score from the feature row."""

    mapping = {
        "casual_meetup": feature.casual_meetup_score,
        "lesson": feature.lesson_spot_score,
        "activity": feature.group_activity_score,
        "night_social": feature.night_liveliness_score,
        "solo_healing": feature.solo_activity_score,
    }
    return clip01(float(mapping.get(spot_type) or 0.0))


def build(db, dataset_version: str, target_city: str) -> dict[str, int]:
    """Generate spot_seed_dataset rows.

    Returns ``{"rows": N}``.
    """

    stmt = (
        select(RegionFeature)
        .join(RegionMaster, RegionMaster.id == RegionFeature.region_id)
        .where(RegionFeature.dataset_version == dataset_version)
        .where(RegionMaster.is_active.is_(True))
        .where(RegionMaster.target_city == target_city)
    )
    features = list(db.scalars(stmt).all())
    if not features:
        logger.warning(
            "build_spot_weights: no region_feature rows for dataset_version=%s "
            "target_city=%s",
            dataset_version,
            target_city,
        )
        return {"rows": 0}

    upserted = 0
    for feature in features:
        for spot_type, categories in SPOT_TYPES.items():
            spot_suitability = _spot_type_suitability(feature, spot_type)
            for category in categories:
                supply = _score_from_feature(feature, category)
                # Demand = 70% spot suitability + 30% category supply as a
                # stand-in until real activity data is available.
                demand = clip01(
                    weighted_avg(
                        [
                            (spot_suitability, 0.7),
                            (supply, 0.3),
                        ]
                    )
                )
                final_weight = clip01(
                    weighted_avg(
                        [
                            (demand, 0.55),
                            (supply, 0.45),
                        ]
                    )
                )

                values: dict[str, Any] = {
                    "dataset_version": dataset_version,
                    "region_id": feature.region_id,
                    "spot_type": spot_type,
                    "category": category,
                    "expected_demand_score": demand,
                    "expected_supply_score": supply,
                    "recommended_capacity": _DEFAULT_CAPACITY[spot_type],
                    "recommended_time_slots": _DEFAULT_TIME_SLOTS[spot_type],
                    "price_band": _DEFAULT_PRICE_BAND[spot_type],
                    "final_weight": final_weight,
                    "payload_json": {
                        "spot_suitability": spot_suitability,
                        "formula": "final=0.55*demand+0.45*supply",
                    },
                }
                stmt = pg_insert(SpotSeedDataset).values(**values)
                update_cols = {
                    col: stmt.excluded[col]
                    for col in values.keys()
                    if col
                    not in {
                        "dataset_version",
                        "region_id",
                        "spot_type",
                        "category",
                    }
                }
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_spot_seed_version_region_type_category",
                    set_=update_cols,
                )
                db.execute(stmt)
                upserted += 1

    db.commit()
    logger.info(
        "build_spot_weights: dataset_version=%s rows=%d", dataset_version, upserted
    )
    return {"rows": upserted}
