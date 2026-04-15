"""Batch-service database engine and session factory.

This module owns the **writable** database used by local-context-builder
for its own tables (region_master, place_raw_kakao, region_feature, ...).
Do NOT import the real-service engine here — that lives in
``app.db_readonly`` and is intentionally isolated to prevent accidental
writes against the production database.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all batch-service ORM models.

    Every model under ``app/models`` must inherit from this class so
    that ``Base.metadata`` is used as Alembic's ``target_metadata``.
    """


def _build_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )


engine: Engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def get_session():
    """FastAPI dependency: yield a batch-DB session and close it."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
