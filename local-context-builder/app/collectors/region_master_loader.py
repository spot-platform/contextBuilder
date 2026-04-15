"""Helpers that read :class:`RegionMaster` rows for collector jobs.

The collector should never build ad-hoc queries against ``region_master``
inline: the scoping rules (plan §4-1, §17) always require filtering by
``target_city`` and ``is_active=True``. Centralising the query here makes
it trivial to audit.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.region import RegionMaster


def load_active(db: Session, target_city: str) -> list[RegionMaster]:
    """Return every active region for ``target_city`` ordered by id."""

    stmt = (
        select(RegionMaster)
        .where(RegionMaster.target_city == target_city)
        .where(RegionMaster.is_active.is_(True))
        .order_by(RegionMaster.id)
    )
    return list(db.scalars(stmt).all())


def load_by_codes(db: Session, region_codes: list[str]) -> list[RegionMaster]:
    """Return regions matching a list of ``region_code`` values."""

    if not region_codes:
        return []
    stmt = (
        select(RegionMaster)
        .where(RegionMaster.region_code.in_(region_codes))
        .order_by(RegionMaster.id)
    )
    return list(db.scalars(stmt).all())
