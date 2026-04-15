"""Category-based collection for a single region.

Plan §5-4 fixes the MVP category set to the five codes that matter for
spot formation: food, cafes, culture, tourism, and academies. For each
region we probe once (FD6 at 1km), decide whether to split into a 2×2
grid, then fan out across the five codes for every resulting cell.

The collector never commits — the caller (``full_rebuild``) owns the
transaction boundary so a region can be rolled back cleanly if
anything downstream fails.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.collectors._upsert import upsert_docs
from app.collectors.grid_strategy import probe_and_plan

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.collectors.kakao_local_client import KakaoLocalClient
    from app.models.region import RegionMaster

logger = logging.getLogger(__name__)

# Plan §5-4: the ✅ marked codes only. Do not bolt others on without
# updating the plan and the processor's mapping rules.
CATEGORY_CODES: tuple[str, ...] = ("FD6", "CE7", "CT1", "AT4", "AC5")


def collect_region_categories(
    db: "Session",
    client: "KakaoLocalClient",
    region: "RegionMaster",
    batch_id: str,
) -> int:
    """Sweep every category code for every grid cell of ``region``.

    Returns the number of documents upserted.
    """

    cells = probe_and_plan(region, client)
    logger.info(
        "region %s category sweep: cells=%d codes=%d",
        region.region_code,
        len(cells),
        len(CATEGORY_CODES),
    )

    total = 0
    for cell_lng, cell_lat, cell_radius in cells:
        for code in CATEGORY_CODES:
            docs = client.search_category(
                code, x=cell_lng, y=cell_lat, radius=cell_radius
            )
            if not docs:
                continue
            upserted = upsert_docs(
                db,
                docs,
                region=region,
                search_type="category",
                search_query=None,
                batch_id=batch_id,
            )
            total += upserted
    return total
