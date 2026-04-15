"""Full-rebuild collection job.

Entry point: :func:`run_full_rebuild`. Iterates every active region for
``target_city``, runs category + keyword sweeps, and records the batch's
region-level outcomes. Per plan §6 STEP 2, a single region's failure
must not abort the batch — we catch broad exceptions, log them, record
the failure in the return value, and continue.

Each region runs inside its own short-lived ``SessionLocal`` session so
that a rollback triggered by one region cannot poison sibling regions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update

from app.collectors.category_collector import collect_region_categories
from app.collectors.kakao_local_client import KakaoLocalClient, get_kakao_client
from app.collectors.keyword_collector import collect_region_keywords
from app.collectors.region_master_loader import load_active
from app.db import SessionLocal
from app.models.region import RegionMaster

logger = logging.getLogger(__name__)


def run_full_rebuild(
    target_city: str = "suwon",
    *,
    client: KakaoLocalClient | None = None,
) -> dict[str, Any]:
    """Run a full Kakao collection sweep for ``target_city``.

    Returns a summary dict with the batch id, counts, and failure list.
    Never raises on a per-region error — inspect ``failed`` instead.
    """

    batch_id = _new_batch_id()
    owned_client = client is None
    client = client or get_kakao_client()

    logger.info(
        "full_rebuild start batch_id=%s target_city=%s", batch_id, target_city
    )

    try:
        with SessionLocal() as read_db:
            regions = load_active(read_db, target_city)
    except Exception:
        if owned_client:
            client.close()
        raise

    failed: list[dict[str, Any]] = []
    total_upserts = 0
    processed_regions = 0

    try:
        for region in regions:
            region_id = region.id
            region_code = region.region_code
            try:
                with SessionLocal() as db:
                    cat_n = collect_region_categories(db, client, region, batch_id)
                    kw_n = collect_region_keywords(db, client, region, batch_id)
                    # Advance the region's collection watermark so the
                    # incremental job can tell fresh regions apart.
                    db.execute(
                        update(RegionMaster)
                        .where(RegionMaster.id == region_id)
                        .values(last_collected_at=datetime.now(timezone.utc))
                    )
                    db.commit()
                total_upserts += cat_n + kw_n
                processed_regions += 1
                logger.info(
                    "region %s done category=%d keyword=%d",
                    region_code,
                    cat_n,
                    kw_n,
                )
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

    summary = {
        "batch_id": batch_id,
        "target_city": target_city,
        "regions_total": len(regions),
        "regions_processed": processed_regions,
        "regions_failed": len(failed),
        "places_upserted": total_upserts,
        "failed": failed,
    }
    logger.info(
        "full_rebuild done batch_id=%s processed=%d failed=%d upserts=%d",
        batch_id,
        processed_regions,
        len(failed),
        total_upserts,
    )
    return summary


def _new_batch_id() -> str:
    return f"batch_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
