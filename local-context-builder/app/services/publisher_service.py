"""Plan §11 STEP 10 — dataset_version lifecycle + quality gate.

``publish`` inserts a fresh ``dataset_version`` row with
``status='building'``, runs ``verify_quality`` across the tables the
processor just wrote, then flips ``status`` to ``success`` or
``failed``. Prior successful versions are never modified — even on
failure — because downstream services always read the **latest
success** and must have a stable fallback.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Iterable

from sqlalchemy import func, select

from app.models.dataset_version import DatasetVersion
from app.models.persona_region_weight import PersonaRegionWeight
from app.models.region import RegionMaster
from app.models.region_feature import RegionFeature
from app.models.spot_seed import SpotSeedDataset

logger = logging.getLogger(__name__)


class QualityError(Exception):
    """Raised internally when the publish gate finds a hard failure."""


def _iter_numeric_fields(obj, fields: Iterable[str]):
    for name in fields:
        val = getattr(obj, name, None)
        if val is None:
            continue
        try:
            yield name, float(val)
        except (TypeError, ValueError):
            yield name, float("nan")


def verify_quality(db, version_name: str, target_city: str) -> list[str]:
    """Run the plan §11 quality checks. Returns a list of issue strings;
    an empty list means pass. Warnings (e.g. > 50 % swing vs previous
    version) are only logged, not returned.
    """

    issues: list[str] = []

    # 1. Every active region must have a region_feature row.
    active_ids_stmt = (
        select(RegionMaster.id)
        .where(RegionMaster.is_active.is_(True))
        .where(RegionMaster.target_city == target_city)
    )
    active_ids = {row[0] for row in db.execute(active_ids_stmt).all()}

    feature_ids_stmt = (
        select(RegionFeature.region_id)
        .where(RegionFeature.dataset_version == version_name)
    )
    feature_ids = {row[0] for row in db.execute(feature_ids_stmt).all()}

    missing = active_ids - feature_ids
    if missing:
        issues.append(
            f"region_feature missing for {len(missing)} active region(s)"
        )

    # 2. region_feature.raw_place_count > 0 for every row we wrote.
    stmt = (
        select(RegionFeature)
        .where(RegionFeature.dataset_version == version_name)
    )
    zero_raw = 0
    nonfinite_feature_fields = 0
    numeric_fields = (
        "food_density",
        "cafe_density",
        "activity_density",
        "nightlife_density",
        "lesson_density",
        "park_access_score",
        "culture_score",
        "night_liveliness_score",
        "casual_meetup_score",
        "lesson_spot_score",
        "solo_activity_score",
        "group_activity_score",
        "kakao_raw_score",
        "real_data_score",
        "blended_score",
    )
    for feature in db.scalars(stmt).all():
        if (feature.raw_place_count or 0) <= 0:
            zero_raw += 1
        for _, val in _iter_numeric_fields(feature, numeric_fields):
            if not math.isfinite(val):
                nonfinite_feature_fields += 1
    if zero_raw:
        issues.append(
            f"region_feature.raw_place_count=0 for {zero_raw} row(s)"
        )
    if nonfinite_feature_fields:
        issues.append(
            f"region_feature contains {nonfinite_feature_fields} NaN/inf field(s)"
        )

    # 3. persona_region_weight NaN / inf check.
    prw_stmt = select(PersonaRegionWeight).where(
        PersonaRegionWeight.dataset_version == version_name
    )
    prw_nonfinite = 0
    prw_fields = (
        "affinity_score",
        "create_offer_score",
        "create_request_score",
        "join_score",
    )
    for row in db.scalars(prw_stmt).all():
        for _, val in _iter_numeric_fields(row, prw_fields):
            if not math.isfinite(val):
                prw_nonfinite += 1
    if prw_nonfinite:
        issues.append(
            f"persona_region_weight contains {prw_nonfinite} NaN/inf value(s)"
        )

    # 4. spot_seed_dataset.final_weight ∈ [0,1] and no NaN.
    sss_stmt = select(SpotSeedDataset).where(
        SpotSeedDataset.dataset_version == version_name
    )
    out_of_range = 0
    for row in db.scalars(sss_stmt).all():
        val = row.final_weight
        if val is None or not math.isfinite(float(val)):
            out_of_range += 1
            continue
        if float(val) < 0.0 or float(val) > 1.0:
            out_of_range += 1
    if out_of_range:
        issues.append(
            f"spot_seed_dataset.final_weight invalid for {out_of_range} row(s)"
        )

    # 5. (warning only) > 50% swing vs previous success.
    prev = db.execute(
        select(DatasetVersion.version_name)
        .where(DatasetVersion.status == "success")
        .where(DatasetVersion.target_city == target_city)
        .where(DatasetVersion.version_name != version_name)
        .order_by(DatasetVersion.built_at.desc())
        .limit(1)
    ).first()
    if prev is not None:
        prev_version = prev[0]
        cur_count = db.execute(
            select(func.count(RegionFeature.id)).where(
                RegionFeature.dataset_version == version_name
            )
        ).scalar_one()
        prev_count = db.execute(
            select(func.count(RegionFeature.id)).where(
                RegionFeature.dataset_version == prev_version
            )
        ).scalar_one()
        if prev_count and cur_count:
            change = abs(cur_count - prev_count) / prev_count
            if change > 0.5:
                logger.warning(
                    "verify_quality: region_feature row count swing %.0f%% "
                    "vs previous success %s (%d → %d)",
                    change * 100,
                    prev_version,
                    prev_count,
                    cur_count,
                )

    return issues


def publish(
    db,
    version_name: str,
    target_city: str,
    build_type: str = "full",
) -> DatasetVersion:
    """Insert a ``dataset_version`` row and transition status.

    - Starts as ``status='building'``.
    - Calls :func:`verify_quality`.
    - Empty issue list → ``status='success'``, ``built_at=now()``.
    - Non-empty issue list → ``status='failed'``, ``error_message``
      populated with newline-joined issues.

    Never touches previous success rows.
    """

    # Check for existing row (re-run of same version_name).
    existing = db.execute(
        select(DatasetVersion).where(
            DatasetVersion.version_name == version_name
        )
    ).scalar_one_or_none()
    if existing is None:
        version = DatasetVersion(
            version_name=version_name,
            build_type=build_type,
            target_city=target_city,
            status="building",
        )
        db.add(version)
        db.flush()
    else:
        version = existing
        version.status = "building"
        version.error_message = None
        version.build_type = build_type
        version.target_city = target_city

    # Compute count summaries for the manifest.
    region_count = db.execute(
        select(func.count(RegionFeature.id)).where(
            RegionFeature.dataset_version == version_name
        )
    ).scalar_one()
    place_count = db.execute(
        select(func.count(SpotSeedDataset.id)).where(
            SpotSeedDataset.dataset_version == version_name
        )
    ).scalar_one()
    version.region_count = int(region_count or 0)
    version.place_count = int(place_count or 0)

    issues = verify_quality(db, version_name, target_city)
    if issues:
        version.status = "failed"
        version.error_message = "\n".join(issues)
        db.commit()
        logger.error(
            "publish: dataset_version=%s FAILED with %d issue(s): %s",
            version_name,
            len(issues),
            issues,
        )
        return version

    version.status = "success"
    version.built_at = datetime.utcnow()
    db.commit()
    logger.info(
        "publish: dataset_version=%s SUCCESS regions=%d places=%d",
        version_name,
        version.region_count,
        version.place_count,
    )
    return version
