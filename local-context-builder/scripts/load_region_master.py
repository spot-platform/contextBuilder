"""Upsert region_master rows from a CSV seed file.

Usage::

    python -m scripts.load_region_master
    python -m scripts.load_region_master --csv data/region_master_suwon.csv

The CSV is the human-editable source of truth for regions. Lines that
start with ``#`` are treated as comments and skipped. Empty coordinate
cells become ``NULL``. All rows loaded by this script get
``target_city='suwon'`` and ``is_active=True`` — the plan
(§4-1) says Suwon is the only activated city at v1.0.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.models.region import RegionMaster

DEFAULT_CSV = Path(__file__).resolve().parent.parent / "data" / "region_master_suwon.csv"

_FLOAT_FIELDS = (
    "center_lng",
    "center_lat",
    "bbox_min_lng",
    "bbox_min_lat",
    "bbox_max_lng",
    "bbox_max_lat",
    "area_km2",
)


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    return float(value)


def _iter_rows(csv_path: Path):
    with csv_path.open(encoding="utf-8") as fh:
        # Strip ``#`` comment lines *before* feeding to csv.DictReader so the
        # header still gets detected correctly.
        lines = [line for line in fh if not line.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    for row in reader:
        # Skip fully empty rows (trailing blank lines).
        if not any((v or "").strip() for v in row.values()):
            continue
        yield row


def load_region_master(csv_path: Path) -> int:
    """Upsert every row from ``csv_path``. Returns the number of rows loaded."""

    if not csv_path.exists():
        raise FileNotFoundError(f"region master CSV not found: {csv_path}")

    rows_loaded = 0
    session = SessionLocal()
    try:
        for row in _iter_rows(csv_path):
            payload: dict = {
                "region_code": row["region_code"].strip(),
                "sido": row["sido"].strip(),
                "sigungu": row["sigungu"].strip(),
                "emd": row["emd"].strip(),
                "target_city": "suwon",
                "is_active": True,
            }
            for field in _FLOAT_FIELDS:
                payload[field] = _to_float(row.get(field))

            # center_lng / center_lat are NOT NULL in the schema. Until the
            # user fills actual coordinates we default to 0.0 so the upsert
            # does not blow up; flag it loudly.
            if payload["center_lng"] is None or payload["center_lat"] is None:
                print(
                    f"[warn] region_code={payload['region_code']} has no centroid; "
                    "using (0.0, 0.0) placeholder. Fill in real coords before publish.",
                    file=sys.stderr,
                )
                payload["center_lng"] = payload["center_lng"] or 0.0
                payload["center_lat"] = payload["center_lat"] or 0.0

            stmt = pg_insert(RegionMaster).values(**payload)
            update_cols = {
                k: stmt.excluded[k]
                for k in payload
                if k != "region_code"
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=[RegionMaster.region_code],
                set_=update_cols,
            )
            session.execute(stmt)
            rows_loaded += 1

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return rows_loaded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"CSV path (default: {DEFAULT_CSV})",
    )
    args = parser.parse_args()
    count = load_region_master(args.csv)
    print(f"[ok] loaded {count} region_master rows from {args.csv}")


if __name__ == "__main__":
    main()
