"""Health checks for the batch service.

Used by the ``/admin/health`` endpoint and ad-hoc operator scripts. The
functions are intentionally fail-soft: every check returns a ``status``
string (``"ok"`` / ``"error"``) and an ``error`` message instead of
raising, so the health endpoint can aggregate them without short-
circuiting on the first failure.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def check_db(db: Optional[Session] = None) -> dict[str, Any]:
    """Ping the batch-service Postgres with ``SELECT 1``.

    Accepts an optional session so callers can reuse one; otherwise we
    open a short-lived session from the module-level factory. Import is
    deferred to avoid dragging :mod:`app.db` (which evaluates
    ``get_settings()`` at import time) into test environments that only
    exercise the pure helpers.
    """

    try:
        if db is not None:
            db.execute(text("SELECT 1"))
            return {"status": "ok"}
        from app.db import SessionLocal  # local import on purpose

        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - surface as health payload
        logger.exception("check_db failed")
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def check_redis() -> dict[str, Any]:
    """Ping Redis using the URL from :class:`Settings`.

    Uses the ``redis`` client directly instead of going through Celery,
    so we do not force Celery worker startup just to check liveness.
    """

    try:
        import redis  # local import so the test suite does not require it

        from app.config import get_settings

        client = redis.Redis.from_url(get_settings().redis_url)
        client.ping()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - surface as health payload
        logger.exception("check_redis failed")
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def latest_dataset_version(db: Session) -> dict[str, Any] | None:
    """Return the most recent successful ``dataset_version`` row, or None."""

    from app.models.dataset_version import DatasetVersion

    row = db.execute(
        select(DatasetVersion)
        .where(DatasetVersion.status == "success")
        .order_by(DatasetVersion.built_at.desc().nullslast())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "version_name": row.version_name,
        "build_type": row.build_type,
        "target_city": row.target_city,
        "built_at": row.built_at.isoformat() if row.built_at else None,
        "region_count": row.region_count,
        "place_count": row.place_count,
        "status": row.status,
    }
