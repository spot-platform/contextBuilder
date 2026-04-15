"""Shared upsert helper for ``place_raw_kakao``.

Both :mod:`app.collectors.category_collector` and
:mod:`app.collectors.keyword_collector` call :func:`upsert_docs`. The
upsert key is ``(source_place_id, region_id)`` per the column contract
(``uq_place_raw_source_region``). Every document row fills the columns
listed in ``column_contract.md`` §place_raw_kakao — including
``search_type``, ``search_query``, ``batch_id`` and ``raw_json``, which
are explicit requirements from plan §6 STEP 2.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.place_raw import PlaceRawKakao
from app.models.region import RegionMaster

logger = logging.getLogger(__name__)


def upsert_docs(
    db: Session,
    docs: Iterable[dict[str, Any]],
    *,
    region: RegionMaster,
    search_type: str,
    batch_id: str,
    search_query: str | None = None,
) -> int:
    """Upsert ``docs`` into ``place_raw_kakao``.

    Returns the number of rows actually processed (some documents may
    be skipped when mandatory fields are missing).
    """

    processed = 0
    for doc in docs:
        row = _build_row(
            doc,
            region=region,
            search_type=search_type,
            search_query=search_query,
            batch_id=batch_id,
        )
        if row is None:
            continue

        stmt = pg_insert(PlaceRawKakao).values(**row)
        # On conflict, refresh every mutable field so re-running the
        # same batch with new Kakao data always wins. We intentionally
        # do NOT touch ``id`` or the PK-adjacent columns.
        excluded = stmt.excluded
        update_cols = {
            "place_name": excluded.place_name,
            "category_name": excluded.category_name,
            "category_group_code": excluded.category_group_code,
            "category_group_name": excluded.category_group_name,
            "phone": excluded.phone,
            "address_name": excluded.address_name,
            "road_address_name": excluded.road_address_name,
            "x": excluded.x,
            "y": excluded.y,
            "place_url": excluded.place_url,
            "distance": excluded.distance,
            "raw_json": excluded.raw_json,
            "search_type": excluded.search_type,
            "search_query": excluded.search_query,
            "batch_id": excluded.batch_id,
            "collected_at": func.now(),
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_place_id", "region_id"],
            set_=update_cols,
        )
        db.execute(stmt)
        processed += 1
    return processed


def _build_row(
    doc: dict[str, Any],
    *,
    region: RegionMaster,
    search_type: str,
    search_query: str | None,
    batch_id: str,
) -> dict[str, Any] | None:
    """Convert a raw Kakao document into a ``place_raw_kakao`` row dict."""

    source_place_id = doc.get("id")
    place_name = doc.get("place_name")
    if not source_place_id or not place_name:
        logger.warning(
            "kakao doc missing id/place_name, skipping: keys=%s", sorted(doc.keys())
        )
        return None

    x_val = _safe_float(doc.get("x"))
    y_val = _safe_float(doc.get("y"))
    if x_val is None or y_val is None:
        logger.warning(
            "kakao doc %s missing x/y, skipping: x=%r y=%r",
            source_place_id,
            doc.get("x"),
            doc.get("y"),
        )
        return None

    return {
        "region_id": region.id,
        "source_place_id": str(source_place_id),
        "place_name": place_name,
        "category_name": doc.get("category_name"),
        "category_group_code": doc.get("category_group_code") or None,
        "category_group_name": doc.get("category_group_name") or None,
        "phone": doc.get("phone") or None,
        "address_name": doc.get("address_name") or None,
        "road_address_name": doc.get("road_address_name") or None,
        "x": x_val,
        "y": y_val,
        "place_url": doc.get("place_url") or None,
        "distance": doc.get("distance") or None,
        "raw_json": doc,
        "search_type": search_type,
        "search_query": search_query,
        "batch_id": batch_id,
    }


def _safe_float(value: Any) -> float | None:
    """Kakao returns coordinates as strings; coerce defensively."""

    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
