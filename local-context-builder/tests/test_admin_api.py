"""Admin API smoke tests (plan §14 — 14 endpoints).

These tests validate three things:

1. Auth: every non-``/health`` endpoint returns 401 without a valid
   ``X-Admin-Key`` header.
2. Routing: every POST endpoint forwards to the correct Celery task
   name (we monkeypatch ``celery.send_task`` to avoid needing a broker).
3. Read-path plumbing: ``get_db`` is overridden with an in-memory
   sqlite session so no Postgres is required. JSONB columns fall back
   to TEXT on sqlite, which is fine for smoke-level assertions.

Integration-level checks against a real Postgres live in
``test_integration_pipeline.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# conftest.py already seeds required env vars before this import.
from app.api import admin as admin_module
from app.api.admin import get_db, router
from app.main import app


# ---------------------------------------------------------------------------
# In-memory DB fixture — sqlite with JSONB stubs via JSON
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def test_engine(monkeypatch):
    """Build a throwaway sqlite engine and create ``Base.metadata``.

    JSONB → TEXT/JSON fallback is handled by sqlalchemy automatically
    because ``JSONB`` inherits from ``JSON`` at the compile level when
    the dialect is sqlite. That's enough for the smoke-path assertions
    in this file.
    """

    # Inject JSONB -> JSON compilation so place_raw_kakao / region_feature / etc
    # can be created on sqlite without a real Postgres.
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")  # type: ignore[misc]
    def _compile_jsonb(type_, compiler, **kw):  # pragma: no cover - compile path
        return "JSON"

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )

    from app.db import Base
    import app.models  # noqa: F401 - registers mappers on Base.metadata

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(test_engine):
    """FastAPI TestClient with ``get_db`` overridden to the sqlite engine."""

    TestingSession = sessionmaker(bind=test_engine, expire_on_commit=False, future=True)

    def _override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def mock_celery(monkeypatch):
    """Replace ``celery.send_task`` with an in-memory recorder."""

    calls: list[dict[str, Any]] = []

    class _FakeAsyncResult:
        def __init__(self, task_id: str):
            self.id = task_id

    def _fake_send_task(name, args=None, kwargs=None, **_ignored):
        call = {"name": name, "args": list(args or []), "kwargs": dict(kwargs or {})}
        calls.append(call)
        return _FakeAsyncResult(f"fake-task-{len(calls)}")

    monkeypatch.setattr(admin_module.celery, "send_task", _fake_send_task)
    return calls


# ---------------------------------------------------------------------------
# Endpoint inventory — a single source of truth matched against plan §14
# ---------------------------------------------------------------------------


EXPECTED_ROUTES = {
    ("POST", "/admin/bootstrap"),
    ("POST", "/admin/full-rebuild"),
    ("POST", "/admin/incremental-refresh"),
    ("POST", "/admin/build-features"),
    ("POST", "/admin/publish"),
    ("GET", "/admin/status"),
    ("GET", "/admin/dataset/latest"),
    ("GET", "/admin/dataset/versions"),
    ("GET", "/admin/region/{region_id}"),
    ("GET", "/admin/region/{region_id}/places"),
    ("GET", "/admin/persona-region/{persona_type}/{region_id}"),
    ("GET", "/admin/health"),
    ("GET", "/admin/metrics"),
}


def test_all_plan_14_routes_exist():
    """Every endpoint from plan §14 must be wired up."""

    registered = set()
    for route in router.routes:
        for method in getattr(route, "methods", set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            registered.add((method, route.path))

    missing = EXPECTED_ROUTES - registered
    assert not missing, f"missing admin routes: {missing}"


def test_health_is_unauthenticated(client):
    resp = client.get("/admin/health")
    # 200 (happy) or 503 (e.g. redis not up); must NOT be 401.
    assert resp.status_code != 401
    body = resp.json()
    assert "status" in body
    assert "checks" in body


# ---------------------------------------------------------------------------
# Auth enforcement — every protected route 401's without header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,url,body",
    [
        ("POST", "/admin/bootstrap", {}),
        ("POST", "/admin/full-rebuild", {"target_city": "suwon"}),
        ("POST", "/admin/incremental-refresh", {"target_city": "suwon"}),
        ("POST", "/admin/build-features", {"target_city": "suwon"}),
        (
            "POST",
            "/admin/publish",
            {"target_city": "suwon", "dataset_version": "v_test"},
        ),
        ("GET", "/admin/status", None),
        ("GET", "/admin/dataset/latest", None),
        ("GET", "/admin/dataset/versions", None),
        ("GET", "/admin/region/1", None),
        ("GET", "/admin/region/1/places", None),
        ("GET", "/admin/persona-region/explorer/1", None),
        ("GET", "/admin/metrics", None),
    ],
)
def test_protected_endpoints_require_admin_key(client, method, url, body):
    if method == "POST":
        resp = client.post(url, json=body)
    else:
        resp = client.get(url)
    assert resp.status_code == 401, f"{method} {url}: expected 401, got {resp.status_code}"


# ---------------------------------------------------------------------------
# POST endpoints — with valid key they send the right Celery task
# ---------------------------------------------------------------------------


HEADERS = {"X-Admin-Key": "test-admin-key"}


def test_bootstrap_queues_task(client, mock_celery):
    resp = client.post("/admin/bootstrap", json={"target_city": "suwon"}, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_name"] == "jobs.bootstrap_regions"
    assert body["status"] == "queued"
    assert body["task_id"].startswith("fake-task-")
    assert mock_celery[-1]["name"] == "jobs.bootstrap_regions"


def test_full_rebuild_queues_task(client, mock_celery):
    resp = client.post(
        "/admin/full-rebuild", json={"target_city": "suwon"}, headers=HEADERS
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_name"] == "jobs.full_rebuild"
    assert mock_celery[-1]["args"] == ["suwon"]


def test_incremental_refresh_queues_task(client, mock_celery):
    resp = client.post(
        "/admin/incremental-refresh",
        json={"target_city": "suwon", "force": True},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert mock_celery[-1]["name"] == "jobs.incremental_refresh"
    assert mock_celery[-1]["args"] == ["suwon", True]


def test_build_features_queues_task(client, mock_celery):
    resp = client.post(
        "/admin/build-features",
        json={"target_city": "suwon", "dataset_version": "v_test_1"},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert mock_celery[-1]["name"] == "jobs.build_all_features"
    assert mock_celery[-1]["args"] == ["suwon", "v_test_1"]


def test_publish_queues_task(client, mock_celery):
    resp = client.post(
        "/admin/publish",
        json={"target_city": "suwon", "dataset_version": "v_test_1"},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert mock_celery[-1]["name"] == "jobs.publish_dataset"
    # Default build_type = "full"
    assert mock_celery[-1]["args"] == ["suwon", "v_test_1", "full"]


def test_publish_requires_dataset_version(client, mock_celery):
    """Pydantic should reject missing dataset_version (422)."""

    resp = client.post(
        "/admin/publish", json={"target_city": "suwon"}, headers=HEADERS
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET endpoints — empty-DB smoke
# ---------------------------------------------------------------------------


def test_status_empty_db(client):
    resp = client.get("/admin/status", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_regions"] == 0
    assert body["active_regions"] == 0
    assert body["total_places_raw"] == 0
    assert body["total_places_normalized"] == 0


def test_dataset_latest_404_when_empty(client):
    resp = client.get("/admin/dataset/latest", headers=HEADERS)
    assert resp.status_code == 404


def test_dataset_versions_empty(client):
    resp = client.get("/admin/dataset/versions", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"versions": []}


def test_region_detail_404(client):
    resp = client.get("/admin/region/999", headers=HEADERS)
    assert resp.status_code == 404


def test_region_places_404(client):
    resp = client.get("/admin/region/999/places", headers=HEADERS)
    assert resp.status_code == 404


def test_persona_region_404(client):
    resp = client.get("/admin/persona-region/explorer/1", headers=HEADERS)
    assert resp.status_code == 404


def test_metrics_empty(client):
    resp = client.get("/admin/metrics", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_regions"] == 0
    assert body["dataset_versions"] == 0


# ---------------------------------------------------------------------------
# Task name registry must match send_task calls
# ---------------------------------------------------------------------------


def test_task_names_match_registry():
    """Every send_task name inside admin.py must be listed in _tasks."""

    import inspect
    import re

    from app.jobs._tasks import REGISTERED_TASK_NAMES

    src = inspect.getsource(admin_module)
    referenced = {r.strip('"') for r in re.findall(r'"jobs\.[a-z_]+"', src)}
    assert referenced, "admin.py contains no jobs.* task names — wiring broken"
    unknown = referenced - set(REGISTERED_TASK_NAMES)
    assert not unknown, f"admin.py calls unregistered tasks: {unknown}"
