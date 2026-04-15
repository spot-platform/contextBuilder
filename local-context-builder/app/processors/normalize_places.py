"""Plan §6 STEP 3 — ``place_raw_kakao`` → ``place_normalized``.

Responsibilities:

1. Pull un-processed raw rows (optionally scoped to a ``batch_id``).
2. Drop exact-duplicate ``source_place_id`` inside the batch.
3. Apply ``category_mapping_rule`` (via ``category_mapper``) to assign
   ``primary_category`` and the ``is_*`` tag dict.
4. Derive ``is_night_friendly`` and ``is_group_friendly``.
5. Upsert ``place_normalized`` keyed by ``(source, source_place_id)``.
6. Log (do NOT auto-merge) spatial+name-similarity near-duplicates.
7. Report the unmapped ratio; warn > 15 %.

The real-service DB is never touched here; this step only reads/writes
``local-context-builder``'s own tables.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.place_normalized import PlaceNormalized
from app.models.place_raw import PlaceRawKakao
from app.processors.category_mapper import INTERNAL_TAGS, load_rules, map_place

logger = logging.getLogger(__name__)

_NIGHT_KEYWORDS = ("주점", "바", "포차", "술집")
_GROUP_KEYWORDS = ("파티", "단체", "모임", "파티룸")

# ~10m in degrees (very rough, good enough for dedup flagging).
_APPROX_10M_DEG = 10.0 / 111_000.0
_NAME_SIMILARITY_THRESHOLD = 0.80


def _derived_tags(
    primary_category: str, tags: dict[str, bool], category_name: str | None
) -> tuple[bool, bool]:
    cat = (category_name or "").lower()
    is_night_friendly = bool(
        tags.get("is_nightlife")
        or any(k in cat for k in _NIGHT_KEYWORDS)
    )
    is_group_friendly = bool(
        tags.get("is_activity")
        or tags.get("is_lesson")
        or any(k in cat for k in _GROUP_KEYWORDS)
    )
    return is_night_friendly, is_group_friendly


def _fetch_raw_rows(db, batch_id: str | None) -> list[PlaceRawKakao]:
    stmt = select(PlaceRawKakao)
    if batch_id is not None:
        stmt = stmt.where(PlaceRawKakao.batch_id == batch_id)
    else:
        # Skip rows already in place_normalized (same source_place_id).
        already = select(PlaceNormalized.source_place_id)
        stmt = stmt.where(PlaceRawKakao.source_place_id.not_in(already))
    return list(db.scalars(stmt).all())


def _log_near_duplicates(rows: list[tuple[PlaceRawKakao, str]]) -> int:
    """Log spatial+name-similarity duplicates. Auto-merge is forbidden
    in MVP — we only emit a log line so integration-qa can pick it up.
    """

    try:
        from rapidfuzz import fuzz
    except Exception:  # pragma: no cover - rapidfuzz optional at dev time
        logger.debug("rapidfuzz not available; skipping near-duplicate scan")
        return 0

    count = 0
    for i in range(len(rows)):
        raw_a, name_a = rows[i]
        for j in range(i + 1, len(rows)):
            raw_b, name_b = rows[j]
            if abs(raw_a.x - raw_b.x) > _APPROX_10M_DEG:
                continue
            if abs(raw_a.y - raw_b.y) > _APPROX_10M_DEG:
                continue
            ratio = fuzz.ratio(name_a, name_b) / 100.0
            if ratio >= _NAME_SIMILARITY_THRESHOLD:
                logger.info(
                    "near-duplicate candidate: %s <-> %s ratio=%.2f",
                    raw_a.source_place_id,
                    raw_b.source_place_id,
                    ratio,
                )
                count += 1
    return count


def _build_upsert_values(
    raw: PlaceRawKakao,
    primary: str,
    tags: dict[str, bool],
    confidence: float,
) -> dict[str, Any]:
    is_night_friendly, is_group_friendly = _derived_tags(
        primary, tags, raw.category_name
    )
    values: dict[str, Any] = {
        "region_id": raw.region_id,
        "source": "kakao",
        "source_place_id": raw.source_place_id,
        "name": raw.place_name,
        "primary_category": primary,
        "sub_category": raw.category_name,
        "lng": raw.x,
        "lat": raw.y,
        "address_name": raw.address_name,
        "road_address_name": raw.road_address_name,
        "mapping_confidence": confidence,
        "collected_at": raw.collected_at,
        "updated_at": datetime.utcnow(),
        "is_night_friendly": is_night_friendly,
        "is_group_friendly": is_group_friendly,
    }
    for tag in INTERNAL_TAGS:
        values[f"is_{tag}"] = bool(tags.get(f"is_{tag}", False))
    return values


def process_batch(db, batch_id: str | None = None) -> dict[str, int]:
    """Normalize one batch worth of raw rows.

    Returns summary counts ``{"processed", "mapped", "unmapped",
    "duplicates_logged"}``.
    """

    rules = load_rules(db)
    if not rules:
        logger.warning(
            "category_mapping_rule table is empty; every row will be mapped to 'other'"
        )

    raws = _fetch_raw_rows(db, batch_id)

    # 1차 중복: source_place_id 기준 exact match. 같은 raw가 여러 region에서
    # 수집된 경우 첫 레코드만 쓴다.
    seen: set[str] = set()
    unique: list[PlaceRawKakao] = []
    for raw in raws:
        if raw.source_place_id in seen:
            continue
        seen.add(raw.source_place_id)
        unique.append(raw)

    processed = mapped = unmapped = 0
    for raw in unique:
        primary, tags, confidence = map_place(raw, rules)
        values = _build_upsert_values(raw, primary, tags, confidence)

        stmt = pg_insert(PlaceNormalized).values(**values)
        update_cols = {
            col: stmt.excluded[col]
            for col in values.keys()
            if col not in {"source", "source_place_id"}
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_place_norm_source",
            set_=update_cols,
        )
        db.execute(stmt)

        processed += 1
        if primary == "other":
            unmapped += 1
        else:
            mapped += 1

    # 2차 중복은 로그만.
    near_dup_input = [(r, r.place_name or "") for r in unique]
    duplicates_logged = _log_near_duplicates(near_dup_input) if unique else 0

    db.commit()

    if processed:
        unmapped_ratio = unmapped / processed
        if unmapped_ratio > 0.15:
            logger.warning(
                "normalize_places: unmapped ratio %.1f%% exceeds 15%% threshold",
                unmapped_ratio * 100,
            )
        else:
            logger.info(
                "normalize_places: processed=%d mapped=%d unmapped=%d (%.1f%%)",
                processed,
                mapped,
                unmapped,
                unmapped_ratio * 100,
            )

    return {
        "processed": processed,
        "mapped": mapped,
        "unmapped": unmapped,
        "duplicates_logged": duplicates_logged,
    }
