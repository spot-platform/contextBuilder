"""FastAPI admin router — plan §14.

Fourteen endpoints drive the batch service from the outside:

    POST /admin/bootstrap
    POST /admin/full-rebuild
    POST /admin/incremental-refresh
    POST /admin/build-features
    POST /admin/publish
    GET  /admin/status
    GET  /admin/dataset/latest
    GET  /admin/dataset/versions
    GET  /admin/region/{region_id}
    GET  /admin/region/{region_id}/places
    GET  /admin/persona-region/{persona_type}/{region_id}
    GET  /admin/health            (NO auth — liveness probe)
    GET  /admin/metrics

Every POST and every authenticated GET requires the ``X-Admin-Key``
header. Long-running jobs go on Celery via ``celery.send_task`` with
the names registered in ``app/jobs/_tasks.py``. Read-only GETs query
Postgres directly through a short-lived session.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.celery_app import celery
from app.config import get_settings
from app.models.dataset_version import DatasetVersion
from app.models.persona_region_weight import PersonaRegionWeight
from app.models.place_normalized import PlaceNormalized
from app.models.place_raw import PlaceRawKakao
from app.models.region import RegionMaster
from app.models.region_feature import RegionFeature
from app.models.spot_seed import SpotSeedDataset
from app.monitoring import health_checks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def require_admin_key(
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
) -> None:
    """Header-auth dependency used by every non-``/health`` endpoint."""

    expected = get_settings().admin_api_key
    if not x_admin_key or x_admin_key != expected:
        raise HTTPException(status_code=401, detail="invalid admin key")


def get_db() -> Session:
    """Short-lived session for a single request. Import is deferred
    so importing ``app.api.admin`` does not require a live DB (unit
    tests can override this dep)."""

    from app.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TaskQueuedResponse(BaseModel):
    task_id: str
    task_name: str
    status: str = "queued"
    args: dict[str, Any] = Field(default_factory=dict)


class BootstrapRequest(BaseModel):
    target_city: str = "suwon"


class FullRebuildRequest(BaseModel):
    target_city: str = "suwon"


class IncrementalRefreshRequest(BaseModel):
    target_city: str = "suwon"
    force: bool = False


class BuildFeaturesRequest(BaseModel):
    target_city: str = "suwon"
    dataset_version: Optional[str] = None


class PublishRequest(BaseModel):
    target_city: str = "suwon"
    dataset_version: str
    build_type: str = "full"


class StatusResponse(BaseModel):
    latest_dataset_version: Optional[str]
    latest_status: Optional[str]
    latest_built_at: Optional[str]
    total_regions: int
    active_regions: int
    total_places_raw: int
    total_places_normalized: int


class DatasetVersionSummary(BaseModel):
    version_name: str
    build_type: str
    target_city: Optional[str]
    status: Optional[str]
    built_at: Optional[str]
    region_count: Optional[int]
    place_count: Optional[int]
    error_message: Optional[str] = None


class DatasetVersionsResponse(BaseModel):
    versions: list[DatasetVersionSummary]


class RegionFeatureBrief(BaseModel):
    dataset_version: str
    food_density: Optional[float]
    cafe_density: Optional[float]
    activity_density: Optional[float]
    nightlife_density: Optional[float]
    lesson_density: Optional[float]
    park_access_score: Optional[float]
    culture_score: Optional[float]
    night_liveliness_score: Optional[float]
    casual_meetup_score: Optional[float]
    lesson_spot_score: Optional[float]
    solo_activity_score: Optional[float]
    group_activity_score: Optional[float]
    kakao_raw_score: Optional[float]
    blended_score: Optional[float]
    raw_place_count: Optional[int]
    normalized_place_count: Optional[int]


class RegionDetail(BaseModel):
    id: int
    region_code: str
    sido: str
    sigungu: str
    emd: str
    center_lng: float
    center_lat: float
    area_km2: Optional[float]
    target_city: Optional[str]
    is_active: Optional[bool]
    last_collected_at: Optional[str]
    latest_feature: Optional[RegionFeatureBrief]


class PlaceBrief(BaseModel):
    source_place_id: str
    name: str
    primary_category: str
    sub_category: Optional[str]
    lng: float
    lat: float


class RegionPlacesResponse(BaseModel):
    region_id: int
    count: int
    places: list[PlaceBrief]


class PersonaRegionDetail(BaseModel):
    dataset_version: str
    persona_type: str
    region_id: int
    affinity_score: float
    create_offer_score: Optional[float]
    create_request_score: Optional[float]
    join_score: Optional[float]
    explanation: Optional[dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, dict[str, Any]]


class MetricsResponse(BaseModel):
    total_regions: int
    active_regions: int
    places_raw: int
    places_normalized: int
    dataset_versions: int
    successful_versions: int
    failed_versions: int
    latest_dataset_version: Optional[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _queue(task_name: str, args: list[Any], request_args: dict[str, Any]) -> TaskQueuedResponse:
    logger.info("queue task=%s args=%s", task_name, request_args)
    result = celery.send_task(task_name, args=args)
    return TaskQueuedResponse(
        task_id=str(getattr(result, "id", "")),
        task_name=task_name,
        args=request_args,
    )


def _serialize_feature(feature: RegionFeature) -> RegionFeatureBrief:
    return RegionFeatureBrief(
        dataset_version=feature.dataset_version,
        food_density=feature.food_density,
        cafe_density=feature.cafe_density,
        activity_density=feature.activity_density,
        nightlife_density=feature.nightlife_density,
        lesson_density=feature.lesson_density,
        park_access_score=feature.park_access_score,
        culture_score=feature.culture_score,
        night_liveliness_score=feature.night_liveliness_score,
        casual_meetup_score=feature.casual_meetup_score,
        lesson_spot_score=feature.lesson_spot_score,
        solo_activity_score=feature.solo_activity_score,
        group_activity_score=feature.group_activity_score,
        kakao_raw_score=feature.kakao_raw_score,
        blended_score=feature.blended_score,
        raw_place_count=feature.raw_place_count,
        normalized_place_count=feature.normalized_place_count,
    )


# ---------------------------------------------------------------------------
# POST — long-running batch jobs (queued on Celery)
# ---------------------------------------------------------------------------


@router.post(
    "/bootstrap",
    response_model=TaskQueuedResponse,
    dependencies=[Depends(require_admin_key)],
)
def bootstrap(req: BootstrapRequest = Body(default_factory=BootstrapRequest)):
    """Load ``region_master`` and ``category_mapping_rule`` seeds."""

    return _queue(
        "jobs.bootstrap_regions", args=[], request_args={"target_city": req.target_city}
    )


@router.post(
    "/full-rebuild",
    response_model=TaskQueuedResponse,
    dependencies=[Depends(require_admin_key)],
)
def full_rebuild(req: FullRebuildRequest = Body(default_factory=FullRebuildRequest)):
    """Run a full Kakao collection sweep for ``target_city``."""

    return _queue(
        "jobs.full_rebuild",
        args=[req.target_city],
        request_args={"target_city": req.target_city},
    )


@router.post(
    "/incremental-refresh",
    response_model=TaskQueuedResponse,
    dependencies=[Depends(require_admin_key)],
)
def incremental_refresh(
    req: IncrementalRefreshRequest = Body(default_factory=IncrementalRefreshRequest),
):
    """Refresh only stale regions (plan §12 cadence)."""

    return _queue(
        "jobs.incremental_refresh",
        args=[req.target_city, req.force],
        request_args={"target_city": req.target_city, "force": req.force},
    )


@router.post(
    "/build-features",
    response_model=TaskQueuedResponse,
    dependencies=[Depends(require_admin_key)],
)
def build_features(
    req: BuildFeaturesRequest = Body(default_factory=BuildFeaturesRequest),
):
    """Run normalize → features → persona → spot → publish."""

    return _queue(
        "jobs.build_all_features",
        args=[req.target_city, req.dataset_version],
        request_args={
            "target_city": req.target_city,
            "dataset_version": req.dataset_version,
        },
    )


@router.post(
    "/publish",
    response_model=TaskQueuedResponse,
    dependencies=[Depends(require_admin_key)],
)
def publish(req: PublishRequest):
    """Re-run the publish quality gate on an existing ``dataset_version``."""

    if not req.dataset_version:
        raise HTTPException(
            status_code=400,
            detail="dataset_version is required for /admin/publish",
        )
    return _queue(
        "jobs.publish_dataset",
        args=[req.target_city, req.dataset_version, req.build_type],
        request_args={
            "target_city": req.target_city,
            "dataset_version": req.dataset_version,
            "build_type": req.build_type,
        },
    )


# ---------------------------------------------------------------------------
# GET — synchronous reads
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
def health():
    """Liveness probe — intentionally unauthenticated.

    Aggregates DB + Redis checks so orchestrators (docker, k8s) can
    decide whether to restart the container.
    """

    checks = {
        "db": health_checks.check_db(),
        "redis": health_checks.check_redis(),
    }
    overall = "ok" if all(c.get("status") == "ok" for c in checks.values()) else "error"
    return HealthResponse(status=overall, checks=checks)


@router.get(
    "/status",
    response_model=StatusResponse,
    dependencies=[Depends(require_admin_key)],
)
def status(db: Session = Depends(get_db)):
    """High-level pipeline state for operator dashboards."""

    latest = db.execute(
        select(DatasetVersion)
        .order_by(DatasetVersion.created_at.desc().nullslast())
        .limit(1)
    ).scalar_one_or_none()

    total_regions = db.execute(
        select(func.count(RegionMaster.id))
    ).scalar_one()
    active_regions = db.execute(
        select(func.count(RegionMaster.id)).where(RegionMaster.is_active.is_(True))
    ).scalar_one()
    total_raw = db.execute(select(func.count(PlaceRawKakao.id))).scalar_one()
    total_norm = db.execute(select(func.count(PlaceNormalized.id))).scalar_one()

    return StatusResponse(
        latest_dataset_version=latest.version_name if latest else None,
        latest_status=latest.status if latest else None,
        latest_built_at=(
            latest.built_at.isoformat() if latest and latest.built_at else None
        ),
        total_regions=int(total_regions or 0),
        active_regions=int(active_regions or 0),
        total_places_raw=int(total_raw or 0),
        total_places_normalized=int(total_norm or 0),
    )


@router.get(
    "/dataset/latest",
    response_model=DatasetVersionSummary,
    dependencies=[Depends(require_admin_key)],
)
def dataset_latest(db: Session = Depends(get_db)):
    """Return the most recent successful build. 404 if nothing was ever published."""

    row = db.execute(
        select(DatasetVersion)
        .where(DatasetVersion.status == "success")
        .order_by(DatasetVersion.built_at.desc().nullslast())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="no successful dataset_version")
    return _summary(row)


@router.get(
    "/dataset/versions",
    response_model=DatasetVersionsResponse,
    dependencies=[Depends(require_admin_key)],
)
def dataset_versions(
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=200),
):
    """Return the N most recent dataset_version rows regardless of status."""

    rows = list(
        db.scalars(
            select(DatasetVersion)
            .order_by(DatasetVersion.created_at.desc().nullslast())
            .limit(limit)
        ).all()
    )
    return DatasetVersionsResponse(versions=[_summary(r) for r in rows])


@router.get(
    "/region/{region_id}",
    response_model=RegionDetail,
    dependencies=[Depends(require_admin_key)],
)
def region_detail(
    region_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    """Region metadata + the most recent region_feature row."""

    region = db.get(RegionMaster, region_id)
    if region is None:
        raise HTTPException(status_code=404, detail="region not found")

    feature = db.execute(
        select(RegionFeature)
        .where(RegionFeature.region_id == region_id)
        .order_by(RegionFeature.created_at.desc().nullslast())
        .limit(1)
    ).scalar_one_or_none()

    return RegionDetail(
        id=region.id,
        region_code=region.region_code,
        sido=region.sido,
        sigungu=region.sigungu,
        emd=region.emd,
        center_lng=region.center_lng,
        center_lat=region.center_lat,
        area_km2=region.area_km2,
        target_city=region.target_city,
        is_active=region.is_active,
        last_collected_at=(
            region.last_collected_at.isoformat()
            if region.last_collected_at
            else None
        ),
        latest_feature=_serialize_feature(feature) if feature else None,
    )


@router.get(
    "/region/{region_id}/places",
    response_model=RegionPlacesResponse,
    dependencies=[Depends(require_admin_key)],
)
def region_places(
    region_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Paginated place_normalized listing for a region."""

    region = db.get(RegionMaster, region_id)
    if region is None:
        raise HTTPException(status_code=404, detail="region not found")

    total = db.execute(
        select(func.count(PlaceNormalized.id)).where(
            PlaceNormalized.region_id == region_id
        )
    ).scalar_one()

    rows = list(
        db.scalars(
            select(PlaceNormalized)
            .where(PlaceNormalized.region_id == region_id)
            .order_by(PlaceNormalized.id)
            .offset(offset)
            .limit(limit)
        ).all()
    )

    return RegionPlacesResponse(
        region_id=region_id,
        count=int(total or 0),
        places=[
            PlaceBrief(
                source_place_id=p.source_place_id,
                name=p.name,
                primary_category=p.primary_category,
                sub_category=p.sub_category,
                lng=p.lng,
                lat=p.lat,
            )
            for p in rows
        ],
    )


@router.get(
    "/persona-region/{persona_type}/{region_id}",
    response_model=PersonaRegionDetail,
    dependencies=[Depends(require_admin_key)],
)
def persona_region(
    persona_type: str = Path(..., min_length=1),
    region_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    dataset_version: Optional[str] = Query(default=None),
):
    """Return the persona × region affinity row for the latest (or given) version."""

    stmt = select(PersonaRegionWeight).where(
        PersonaRegionWeight.persona_type == persona_type,
        PersonaRegionWeight.region_id == region_id,
    )
    if dataset_version:
        stmt = stmt.where(PersonaRegionWeight.dataset_version == dataset_version)
    else:
        stmt = stmt.order_by(PersonaRegionWeight.created_at.desc().nullslast())
    stmt = stmt.limit(1)

    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404, detail="persona_region_weight row not found"
        )
    return PersonaRegionDetail(
        dataset_version=row.dataset_version,
        persona_type=row.persona_type,
        region_id=row.region_id,
        affinity_score=row.affinity_score,
        create_offer_score=row.create_offer_score,
        create_request_score=row.create_request_score,
        join_score=row.join_score,
        explanation=row.explanation_json,
    )


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    dependencies=[Depends(require_admin_key)],
)
def metrics(db: Session = Depends(get_db)):
    """Lightweight counters — DB-backed (no Prometheus yet)."""

    total_regions = db.execute(select(func.count(RegionMaster.id))).scalar_one()
    active_regions = db.execute(
        select(func.count(RegionMaster.id)).where(RegionMaster.is_active.is_(True))
    ).scalar_one()
    raw = db.execute(select(func.count(PlaceRawKakao.id))).scalar_one()
    norm = db.execute(select(func.count(PlaceNormalized.id))).scalar_one()
    versions_total = db.execute(
        select(func.count(DatasetVersion.id))
    ).scalar_one()
    versions_ok = db.execute(
        select(func.count(DatasetVersion.id)).where(
            DatasetVersion.status == "success"
        )
    ).scalar_one()
    versions_failed = db.execute(
        select(func.count(DatasetVersion.id)).where(
            DatasetVersion.status == "failed"
        )
    ).scalar_one()
    latest = db.execute(
        select(DatasetVersion.version_name)
        .where(DatasetVersion.status == "success")
        .order_by(DatasetVersion.built_at.desc().nullslast())
        .limit(1)
    ).scalar_one_or_none()

    return MetricsResponse(
        total_regions=int(total_regions or 0),
        active_regions=int(active_regions or 0),
        places_raw=int(raw or 0),
        places_normalized=int(norm or 0),
        dataset_versions=int(versions_total or 0),
        successful_versions=int(versions_ok or 0),
        failed_versions=int(versions_failed or 0),
        latest_dataset_version=latest,
    )


def _summary(row: DatasetVersion) -> DatasetVersionSummary:
    return DatasetVersionSummary(
        version_name=row.version_name,
        build_type=row.build_type,
        target_city=row.target_city,
        status=row.status,
        built_at=row.built_at.isoformat() if row.built_at else None,
        region_count=row.region_count,
        place_count=row.place_count,
        error_message=row.error_message,
    )
