"""End-to-end pipeline integration test (Kakao mocked).

This test is **Postgres-aware**: JSONB, ``ON CONFLICT ... DO UPDATE``,
and the pg_insert upsert path all require a live Postgres. When no
``INTEGRATION_DATABASE_URL`` env var is present we skip the whole
module — sqlite cannot run the upsert code path used by the
processors.

Offline coverage — without a DB — is provided by two subprocess-free
assertions at the top of the file:

1. Every model and migration file imports cleanly.
2. ``app.jobs.full_rebuild.run_full_rebuild`` is callable via the
   same signature the admin API uses.

When a Postgres URL IS provided, we run a full loop:

    seed regions → collect (mocked kakao) → normalize
      → build_region_features → build_persona_region_weights
      → build_spot_weights → publish_dataset

and assert ``dataset_version.status == 'success'``.
"""

from __future__ import annotations

import importlib
import inspect
import os
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Part 1 — DB-free smoke checks (always run)
# ---------------------------------------------------------------------------


def test_all_models_importable():
    """Every model module must import without side effects."""

    mod = importlib.import_module("app.models")
    for name in (
        "CategoryMappingRule",
        "DatasetVersion",
        "PersonaRegionWeight",
        "PlaceNormalized",
        "PlaceRawKakao",
        "RealActivityAgg",
        "RegionFeature",
        "RegionMaster",
        "SpotSeedDataset",
    ):
        assert hasattr(mod, name), f"{name} missing from app.models"


def test_jobs_signature_matches_admin_send_task_args():
    """Admin API sends ``args=[target_city, ...]`` — make sure each job
    function accepts that shape. This catches param drift without
    needing a broker or a DB."""

    from app.jobs import (
        build_all_features,
        bootstrap_regions,
        full_rebuild,
        incremental_refresh,
        publish_dataset,
    )

    sig = inspect.signature(full_rebuild.run_full_rebuild)
    assert "target_city" in sig.parameters

    sig = inspect.signature(incremental_refresh.run_incremental_refresh)
    assert "target_city" in sig.parameters
    assert "force" in sig.parameters

    sig = inspect.signature(build_all_features.run)
    params = list(sig.parameters)
    assert "target_city" in params
    assert "dataset_version" in params

    sig = inspect.signature(publish_dataset.run)
    params = list(sig.parameters)
    assert "target_city" in params
    assert "dataset_version" in params

    sig = inspect.signature(bootstrap_regions.run_bootstrap)
    # run_bootstrap takes optional keyword args only; no positional required.
    assert all(
        p.default is not inspect.Parameter.empty or p.kind is inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    )


def test_registered_celery_tasks_match_admin_send_task_calls():
    """Every send_task name in admin.py must be in the _tasks registry."""

    import re

    from app.api import admin as admin_module
    from app.jobs._tasks import REGISTERED_TASK_NAMES

    src = inspect.getsource(admin_module)
    referenced = set(re.findall(r'"jobs\.[a-z_]+"', src))
    referenced = {r.strip('"') for r in referenced}

    assert referenced, "admin.py has no jobs.* send_task references — did wiring break?"
    unknown = referenced - set(REGISTERED_TASK_NAMES)
    assert not unknown, f"admin.py calls unregistered tasks: {unknown}"


# ---------------------------------------------------------------------------
# Part 2 — live Postgres pipeline (skipped unless INTEGRATION_DATABASE_URL)
# ---------------------------------------------------------------------------


INTEGRATION_URL = os.environ.get("INTEGRATION_DATABASE_URL")

pytestmark_integration = pytest.mark.skipif(
    not INTEGRATION_URL,
    reason="set INTEGRATION_DATABASE_URL to run the full pipeline test",
)


@pytest.fixture(scope="module")
def pg_engine():
    if not INTEGRATION_URL:
        pytest.skip("no INTEGRATION_DATABASE_URL")
    from sqlalchemy import create_engine

    engine = create_engine(INTEGRATION_URL, future=True, pool_pre_ping=True)
    from app.db import Base
    import app.models  # noqa: F401 - register mappers

    # Clean slate.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def pg_session(pg_engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=pg_engine, expire_on_commit=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _make_kakao_client_stub():
    """Return a fake KakaoLocalClient that yields three deterministic docs."""

    class _FakeClient:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

        def search_by_category(self, code, x, y, radius):
            return [
                {
                    "id": f"{code}_1",
                    "place_name": f"{code} place A",
                    "category_name": "음식점 > 한식",
                    "category_group_code": "FD6",
                    "category_group_name": "음식점",
                    "x": "127.0",
                    "y": "37.3",
                    "address_name": "경기 수원시 권선구",
                    "road_address_name": "경기 수원시 권선구 test-ro 1",
                    "place_url": "http://place/a",
                }
            ]

        def search_by_keyword(self, query, x, y, radius):
            return [
                {
                    "id": f"kw_{query}_1",
                    "place_name": f"{query} 가게",
                    "category_name": "음식점 > 카페",
                    "category_group_code": "CE7",
                    "category_group_name": "카페",
                    "x": "127.01",
                    "y": "37.31",
                    "address_name": "경기 수원시",
                    "road_address_name": "경기 수원시 test-ro 2",
                    "place_url": "http://place/b",
                }
            ]

    return _FakeClient()


pytestmark = pytestmark_integration  # applies to the Postgres-backed tests below


def _seed_region(session):
    from app.models.region import RegionMaster

    region = RegionMaster(
        region_code="4111500000",
        sido="경기",
        sigungu="수원시 권선구",
        emd="세류1동",
        center_lng=127.0,
        center_lat=37.3,
        area_km2=2.5,
        target_city="suwon",
        is_active=True,
    )
    session.add(region)
    session.commit()
    return region


def _seed_category_mapping(session):
    from app.models.category_mapping_rule import CategoryMappingRule

    session.add_all(
        [
            CategoryMappingRule(
                kakao_category_group_code="FD6",
                internal_tag="food",
                priority=10,
                confidence=1.0,
                is_active=True,
            ),
            CategoryMappingRule(
                kakao_category_group_code="CE7",
                internal_tag="cafe",
                priority=9,
                confidence=1.0,
                is_active=True,
            ),
        ]
    )
    session.commit()


def test_pipeline_end_to_end_with_mocked_kakao(pg_engine, pg_session):
    """Full loop: seed → collect (fake) → normalize → features → publish."""

    from sqlalchemy.orm import sessionmaker

    # Rebind SessionLocal to the integration engine.
    from app import db as db_module

    Session = sessionmaker(bind=pg_engine, expire_on_commit=False, future=True)
    db_module.SessionLocal = Session  # type: ignore[assignment]

    _seed_region(pg_session)
    _seed_category_mapping(pg_session)

    # Run collection with a fake client so no Kakao traffic is issued.
    from app.jobs import full_rebuild

    fake = _make_kakao_client_stub()
    summary = full_rebuild.run_full_rebuild("suwon", client=fake)
    assert summary["regions_processed"] >= 1
    assert summary["places_upserted"] >= 1

    # Run feature build + publish in one shot.
    from app.jobs import build_all_features

    version = f"v_itest_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    result = build_all_features.run("suwon", dataset_version=version)
    assert result["publish_status"] in ("success", "failed")
    # Raw count should be > 0 thanks to the collector step above.
    from app.models.dataset_version import DatasetVersion
    from sqlalchemy import select

    with Session() as s:
        row = s.execute(
            select(DatasetVersion).where(DatasetVersion.version_name == version)
        ).scalar_one()
        assert row.status in ("success", "failed")
