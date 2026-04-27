"""lcb (local-context-builder) DB → poi_normalized JSON dump utility.

scp 가 lcb 코드를 import 하지 않도록 raw SQL 만 사용.

사용법:
    python scripts/import_lcb_poi_dump.py \\
        --lcb-db-url postgresql://user:pass@host:5432/lcb \\
        --target-region 연무동 \\
        --target-region 인계동 \\
        --out data/poi/poi_normalized_v1.json

또는 도시 단위:
    python scripts/import_lcb_poi_dump.py \\
        --lcb-db-url postgresql+psycopg2://... \\
        --target-city 수원시 \\
        --out data/poi/poi_normalized_v1.json

dependency: ``sqlalchemy`` (이미 scp pyproject.toml 에 있음).
optional: ``psycopg2-binary`` 또는 ``psycopg`` driver — lcb DB driver 에 따라.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

try:
    from sqlalchemy import create_engine, text
except ImportError as exc:  # pragma: no cover
    print(f"sqlalchemy required: {exc}", file=sys.stderr)
    raise


_PLACE_QUERY = text(
    """
    SELECT
      p.id              AS place_id,
      r.emd             AS region_emd,
      p.name            AS name,
      p.primary_category AS primary_category,
      p.sub_category    AS sub_category,
      p.lat             AS lat,
      p.lng             AS lng,
      p.address_name    AS address_name,
      p.road_address_name AS road_address_name,
      COALESCE(p.is_food, false)            AS is_food,
      COALESCE(p.is_cafe, false)            AS is_cafe,
      COALESCE(p.is_activity, false)        AS is_activity,
      COALESCE(p.is_park, false)            AS is_park,
      COALESCE(p.is_culture, false)         AS is_culture,
      COALESCE(p.is_nightlife, false)       AS is_nightlife,
      COALESCE(p.is_lesson, false)          AS is_lesson,
      COALESCE(p.is_night_friendly, false)  AS is_night_friendly,
      COALESCE(p.is_group_friendly, false)  AS is_group_friendly,
      COALESCE(p.mapping_confidence, 1.0)   AS mapping_confidence
    FROM place_normalized p
    JOIN region_master r ON p.region_id = r.id
    WHERE
      ({region_filter})
      AND p.lat IS NOT NULL
      AND p.lng IS NOT NULL
    ORDER BY r.emd, p.id
    """
)

_DATASET_VERSION_QUERY = text(
    """
    SELECT version_name
    FROM dataset_version
    WHERE status = 'success'
    ORDER BY built_at DESC NULLS LAST, id DESC
    LIMIT 1
    """
)


def _build_filter(target_regions: List[str], target_city: Optional[str]) -> tuple[str, dict]:
    if target_regions:
        return "r.emd = ANY(:regions)", {"regions": target_regions}
    if target_city:
        return "r.sigungu = :city", {"city": target_city}
    raise ValueError("must provide --target-region or --target-city")


def _format_query(filter_clause: str) -> text:
    """sqlalchemy.text 는 f-string 인터폴레이션 안 되므로 별도 빌드."""
    raw = str(_PLACE_QUERY).replace("{region_filter}", filter_clause)
    return text(raw)


def dump_pois(
    *,
    lcb_db_url: str,
    target_regions: Optional[List[str]],
    target_city: Optional[str],
    out_path: Path,
) -> dict:
    """lcb DB 쿼리 → JSON 파일. 메타정보 dict 반환."""
    filter_clause, params = _build_filter(target_regions or [], target_city)
    query = _format_query(filter_clause)

    engine = create_engine(lcb_db_url)
    with engine.connect() as conn:
        rows = conn.execute(query, params).mappings().all()
        try:
            ds_row = conn.execute(_DATASET_VERSION_QUERY).first()
            lcb_dataset_version = ds_row[0] if ds_row else None
        except Exception:
            lcb_dataset_version = None

    places = [dict(r) for r in rows]
    payload = {
        "lcb_dataset_version": lcb_dataset_version,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filter": {
            "target_regions": target_regions or None,
            "target_city": target_city,
        },
        "places": places,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    return {
        "count": len(places),
        "lcb_dataset_version": lcb_dataset_version,
        "out": str(out_path),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Dump lcb place_normalized to JSON.")
    p.add_argument("--lcb-db-url", required=True, help="SQLAlchemy URL for lcb DB")
    p.add_argument(
        "--target-region",
        action="append",
        dest="target_regions",
        default=[],
        help="Filter by region_master.emd (한글). repeatable.",
    )
    p.add_argument("--target-city", default=None, help="Filter by region_master.sigungu")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("data/poi/poi_normalized_v1.json"),
        help="Output JSON path",
    )
    args = p.parse_args(argv)

    if not args.target_regions and not args.target_city:
        p.error("must provide --target-region (repeatable) or --target-city")

    info = dump_pois(
        lcb_db_url=args.lcb_db_url,
        target_regions=args.target_regions or None,
        target_city=args.target_city,
        out_path=args.out,
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
