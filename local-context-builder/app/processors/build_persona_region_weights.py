"""Plan §9 STEP 7~8 — persona × region affinity.

Reads the region_feature rows for the given dataset_version + city,
loads the static persona JSON, and writes one row per (persona ×
region) into ``persona_region_weight``.

MVP公式 (단순화):

    affinity_score = category_match(persona, feature)
    create_offer_score   = affinity_score * supply_factor  (MVP: 1.0)
    create_request_score = affinity_score * demand_factor  (MVP: 1.0)
    join_score           = affinity_score

``category_match`` = ∑(persona.category_preferences[k] * density_norm[k])
where ``density_norm`` is read out of ``region_feature.feature_json``
(which ``build_region_features`` wrote with city-relative percentile
ranks).

``time_match`` is a v1.1 feature — the function keeps the hook but
always returns 0.5 (neutral) in MVP.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.persona_region_weight import PersonaRegionWeight
from app.models.region import RegionMaster
from app.models.region_feature import RegionFeature
from app.services.scoring_service import clip01

logger = logging.getLogger(__name__)

# Density norm keys live in feature_json["density_norm"].
_DENSITY_KEYS = ("food", "cafe", "activity", "nightlife", "lesson")
# park / culture are count-based, so we pull them out of the stable columns.


def _load_personas(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"persona_types.json not found at {p}")
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _category_density(feature: RegionFeature, category: str) -> float:
    """Return a normalized [0,1] density for a persona preference key.

    - food/cafe/activity/nightlife/lesson → feature_json["density_norm"]
    - park   → park_access_score
    - culture → culture_score
    """

    if category in _DENSITY_KEYS:
        norms = (feature.feature_json or {}).get("density_norm", {})
        val = norms.get(category, 0.0)
        return clip01(float(val))
    if category == "park":
        return clip01(float(feature.park_access_score or 0.0))
    if category == "culture":
        return clip01(float(feature.culture_score or 0.0))
    return 0.0


def _category_match(
    persona: dict[str, Any], feature: RegionFeature
) -> tuple[float, dict[str, float]]:
    prefs: dict[str, float] = persona.get("category_preferences", {})
    total_weight = sum(float(w) for w in prefs.values() if w)
    if total_weight <= 0:
        return 0.0, {}
    contributions: dict[str, float] = {}
    score = 0.0
    for category, weight in prefs.items():
        w = float(weight or 0.0)
        if w <= 0:
            continue
        dens = _category_density(feature, category)
        contribution = w * dens
        contributions[category] = contribution
        score += contribution
    # ``prefs`` weights sum to 1.0 by construction in persona_types.json,
    # so ``score`` already lives in [0, 1]. Clip defensively anyway.
    return clip01(score), contributions


def build(
    db,
    dataset_version: str,
    target_city: str,
    persona_file: str | Path = "data/persona_types.json",
) -> dict[str, int]:
    """Build persona × region weights for ``dataset_version`` / ``target_city``.

    Returns ``{"personas": N, "regions": N, "upserted": N}``.
    """

    personas = _load_personas(persona_file)

    # Join region_feature with region_master to filter by target_city.
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
            "build_persona_region_weights: no region_feature rows found "
            "for dataset_version=%s target_city=%s",
            dataset_version,
            target_city,
        )
        return {"personas": len(personas), "regions": 0, "upserted": 0}

    upserted = 0
    for persona in personas:
        persona_type = persona["persona_type"]
        for feature in features:
            affinity, contributions = _category_match(persona, feature)
            # MVP factors are all 1.0. v1.1 will pull these from
            # real_activity_agg via feature_service.
            supply_factor = 1.0
            demand_factor = 1.0
            create_offer = clip01(affinity * supply_factor)
            create_request = clip01(affinity * demand_factor)
            join_score = clip01(affinity)

            # Guard against any sneaky NaN/∞ before commit.
            for label, val in (
                ("affinity", affinity),
                ("create_offer", create_offer),
                ("create_request", create_request),
                ("join_score", join_score),
            ):
                if not math.isfinite(val):
                    logger.error(
                        "non-finite %s for persona=%s region=%s; zeroing",
                        label,
                        persona_type,
                        feature.region_id,
                    )

            explanation = {
                "formula": "affinity = sum(pref[k] * density_norm[k])",
                "category_contributions": contributions,
                "supply_factor": supply_factor,
                "demand_factor": demand_factor,
                "time_match": None,  # v1.1
            }

            values = {
                "dataset_version": dataset_version,
                "persona_type": persona_type,
                "region_id": feature.region_id,
                "affinity_score": affinity,
                "create_offer_score": create_offer,
                "create_request_score": create_request,
                "join_score": join_score,
                "explanation_json": explanation,
            }
            stmt = pg_insert(PersonaRegionWeight).values(**values)
            update_cols = {
                col: stmt.excluded[col]
                for col in values.keys()
                if col
                not in {"dataset_version", "persona_type", "region_id"}
            }
            stmt = stmt.on_conflict_do_update(
                constraint="uq_persona_region_weight",
                set_=update_cols,
            )
            db.execute(stmt)
            upserted += 1

    db.commit()
    logger.info(
        "build_persona_region_weights: personas=%d regions=%d upserted=%d",
        len(personas),
        len(features),
        upserted,
    )
    return {
        "personas": len(personas),
        "regions": len(features),
        "upserted": upserted,
    }
