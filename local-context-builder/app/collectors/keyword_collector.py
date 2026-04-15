"""Keyword-based collection for a single region.

Plan §5-5 lists nine keyword templates; MVP (§15) keeps five:
``맛집 / 카페 / 원데이클래스 / 공방 / 운동``. The search is always
anchored on the region centroid — Kakao's keyword API accepts ``x``/``y``
hints which significantly bias results toward the requested 행정동.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.collectors._upsert import upsert_docs

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.collectors.kakao_local_client import KakaoLocalClient
    from app.models.region import RegionMaster

logger = logging.getLogger(__name__)

# MVP keyword templates. Do not widen without bumping MVP scope in §15.
KEYWORD_TEMPLATES: tuple[str, ...] = (
    "맛집",
    "카페",
    "원데이클래스",
    "공방",
    "운동",
)

# Radius hint fed to keyword search. The keyword API still returns
# out-of-radius matches sometimes, but passing x/y/radius tightens the
# result set noticeably.
KEYWORD_RADIUS_M = 2000


def keyword_for(emd: str, template: str) -> str:
    return f"{emd} {template}"


def collect_region_keywords(
    db: "Session",
    client: "KakaoLocalClient",
    region: "RegionMaster",
    batch_id: str,
) -> int:
    """Run every MVP keyword template for ``region``.

    Returns the number of documents upserted.
    """

    total = 0
    for template in KEYWORD_TEMPLATES:
        query = keyword_for(region.emd, template)
        docs = client.search_keyword(
            query,
            x=float(region.center_lng),
            y=float(region.center_lat),
            radius=KEYWORD_RADIUS_M,
        )
        if not docs:
            continue
        upserted = upsert_docs(
            db,
            docs,
            region=region,
            search_type="keyword",
            search_query=query,
            batch_id=batch_id,
        )
        total += upserted
    logger.info(
        "region %s keyword sweep complete upserted=%d",
        region.region_code,
        total,
    )
    return total
