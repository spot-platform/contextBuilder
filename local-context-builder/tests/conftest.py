"""Shared pytest fixtures.

Critical: ``app.config.get_settings`` is cached and reads its values at
first call. Several test modules indirectly import ``app.db`` (which
invokes ``get_settings()`` at import time). If no env vars are set the
import blows up. We seed a safe placeholder set *before* collection
begins by setting env defaults in ``conftest.py`` module top-level —
this file is imported by pytest before any test module.
"""

from __future__ import annotations

import os

# Seed safe defaults so importing app.db / app.celery_app / app.api.admin
# inside tests doesn't require a real .env. Tests that need different
# values can override via monkeypatch + get_settings.cache_clear().
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://lcb:lcb@localhost:5432/lcb_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("KAKAO_REST_API_KEY", "test-kakao-key")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("TARGET_CITY", "suwon")
