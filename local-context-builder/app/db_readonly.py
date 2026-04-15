"""Read-only engine for the live Spring Boot service database.

Safety rules (see plan §2, §17):
- This engine is **never** bound to ``Base.metadata``. No ORM writes
  and no Alembic autogenerate against it.
- ``statement_timeout`` and ``default_transaction_read_only=on`` are
  pushed to the server via libpq ``options`` so the database itself
  rejects any accidental write or long-running query.
- If ``REALSERVICE_DATABASE_URL`` is not configured, the engine stays
  ``None``. Callers must check before use and surface a clear error.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

_engine: Optional[Engine] = None
_SessionFactory = None


def _build_readonly_engine() -> Optional[Engine]:
    settings = get_settings()
    if not settings.realservice_database_url:
        return None

    options = (
        f"-c statement_timeout={settings.realservice_statement_timeout_ms} "
        f"-c default_transaction_read_only=on"
    )
    return create_engine(
        settings.realservice_database_url,
        pool_pre_ping=True,
        future=True,
        connect_args={"options": options},
    )


def get_readonly_engine() -> Optional[Engine]:
    """Return the lazily-initialised read-only engine, or ``None``.

    Lazy init lets the app boot even without real-service credentials
    (useful for local dev and unit tests).
    """

    global _engine, _SessionFactory
    if _engine is None:
        _engine = _build_readonly_engine()
        if _engine is not None:
            _SessionFactory = sessionmaker(
                bind=_engine,
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
                future=True,
            )
    return _engine


def get_readonly_session():
    """Yield a read-only session. Raises if real-service DB is unset."""

    engine = get_readonly_engine()
    if engine is None or _SessionFactory is None:
        raise RuntimeError(
            "REALSERVICE_DATABASE_URL is not configured; "
            "read-only real-service access is unavailable."
        )
    session = _SessionFactory()
    try:
        yield session
    finally:
        session.close()
