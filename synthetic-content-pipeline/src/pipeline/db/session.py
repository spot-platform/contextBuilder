"""Engine + SessionLocal — 환경변수 PIPELINE_DB_URL 로 override 가능."""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DB_URL = "sqlite:///./synthetic_content.db"


def _get_db_url() -> str:
    return os.environ.get("PIPELINE_DB_URL", DEFAULT_DB_URL)


engine = create_engine(_get_db_url(), future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)


def get_session() -> Session:
    """새 세션 반환 (caller가 close 책임)."""
    return SessionLocal()
