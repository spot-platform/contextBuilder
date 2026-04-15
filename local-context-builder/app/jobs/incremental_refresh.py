"""Incremental refresh job — MVP skeleton.

Plan §12 defines the cadence:

- **Active region** (last_collected_at older than 7 days) → refresh.
- **Inactive region** (last_collected_at older than 30 days) → refresh.
- **Brand new region** (``last_collected_at IS NULL``) → refresh.

For MVP we treat every ``is_active=True`` region as "active" (7-day
window). The distinction between active/inactive needs downstream
signals from the real-service DB and is v1.1 work. ``force=True``
refreshes every active region regardless of timestamps.

This job re-uses the same region-by-region worker as
:mod:`app.jobs.full_rebuild`, just with a filtered region list.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import update

from app.collectors.category_collector import collect_region_categories
from app.collectors.kakao_local_client import KakaoLocalClient, get_kakao_client
from app.collectors.keyword_collector import collect_region_keywords
from app.collectors.region_master_loader import load_active
from app.db import SessionLocal
from app.models.region import RegionMaster

logger = logging.getLogger(__name__)

ACTIVE_WINDOW = timedelta(days=7)
INACTIVE_WINDOW = timedelta(days=30)


def run_incremental_refresh(
    target_city: str = "suwon",
    *,
    force: bool = False,
    client: KakaoLocalClient | None = None,
) -> dict[str, Any]:
    """Refresh regions whose ``last_collected_at`` is past the threshold.

    MVP heuristic:

    - ``last_collected_at IS NULL`` → always refresh (new region).
    - otherwise older than :data:`ACTIVE_WINDOW` → refresh.

    ``force=True`` refreshes every active region regardless.
    """

    batch_id = _new_batch_id()
    owned_client = client is None
    client = client or get_kakao_client()
    now = datetime.now(timezone.utc)

    logger.info(
        "incremental_refresh start batch_id=%s target_city=%s force=%s",
        batch_id,
        target_city,
        force,
    )

    try:
        with SessionLocal() as read_db:
            all_regions = load_active(read_db, target_city)
    except Exception:
        if owned_client:
            client.close()
        raise

    targets = [r for r in all_regions if force or _needs_refresh(r, now)]
    skipped = len(all_regions) - len(targets)
    logger.info(
        "incremental_refresh planning target_regions=%d skipped=%d",
        len(targets),
        skipped,
    )

    failed: list[dict[str, Any]] = []
    total_upserts = 0
    processed_regions = 0

    try:
        for region in targets:
            region_id = region.id
            region_code = region.region_code
            try:
                with SessionLocal() as db:
                    cat_n = collect_region_categories(db, client, region, batch_id)
                    kw_n = collect_region_keywords(db, client, region, batch_id)
                    db.execute(
                        update(RegionMaster)
                        .where(RegionMaster.id == region_id)
                        .values(last_collected_at=datetime.now(timezone.utc))
                    )
                    db.commit()
                total_upserts += cat_n + kw_n
                processed_regions += 1
            except Exception as exc:  # noqa: BLE001 - per-region isolation
                logger.exception("region %s failed", region_code)
                failed.append(
                    {
                        "region_id": region_id,
                        "region_code": region_code,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
    finally:
        if owned_client:
            client.close()

    return {
        "batch_id": batch_id,
        "target_city": target_city,
        "force": force,
        "regions_candidates": len(all_regions),
        "regions_targeted": len(targets),
        "regions_skipped": skipped,
        "regions_processed": processed_regions,
        "regions_failed": len(failed),
        "places_upserted": total_upserts,
        "failed": failed,
    }


def _needs_refresh(region: RegionMaster, now: datetime) -> bool:
    last = region.last_collected_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    # MVP treats every active region as "active". v1.1 will read real
    # activity signals to decide between 7-day and 30-day windows.
    return (now - last) > ACTIVE_WINDOW


def _new_batch_id() -> str:
    return f"inc_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
