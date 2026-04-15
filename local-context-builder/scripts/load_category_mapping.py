"""Upsert category_mapping_rule rows from a JSON seed file.

Usage::

    python -m scripts.load_category_mapping
    python -m scripts.load_category_mapping --json data/category_mapping_seed.json

The JSON file is a top-level array. Each element is an object with keys
matching the ``category_mapping_rule`` columns (all optional except
``internal_tag``). Since the table has no natural unique key, this
script upserts on the tuple
``(kakao_category_group_code, kakao_category_pattern, keyword_pattern,
internal_tag)``: delete any existing row matching that tuple, then
insert. This keeps the script idempotent without adding a DB-side
constraint the plan did not specify.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import and_, delete

from app.db import SessionLocal
from app.models.category_mapping_rule import CategoryMappingRule

DEFAULT_JSON = (
    Path(__file__).resolve().parent.parent / "data" / "category_mapping_seed.json"
)

_ALLOWED_KEYS = {
    "kakao_category_group_code",
    "kakao_category_pattern",
    "keyword_pattern",
    "internal_tag",
    "confidence",
    "priority",
    "is_active",
    "notes",
}


def load_category_mapping(json_path: Path) -> int:
    if not json_path.exists():
        raise FileNotFoundError(f"category mapping JSON not found: {json_path}")

    with json_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("category mapping JSON must be a top-level array")

    rows_loaded = 0
    session = SessionLocal()
    try:
        for raw in data:
            if not isinstance(raw, dict):
                raise ValueError(f"each rule must be an object, got: {raw!r}")
            if "internal_tag" not in raw:
                raise ValueError(f"rule is missing internal_tag: {raw!r}")

            payload = {k: v for k, v in raw.items() if k in _ALLOWED_KEYS}

            # Delete any existing row with the same identifying tuple so
            # repeated loads stay idempotent.
            match_clauses = [
                CategoryMappingRule.kakao_category_group_code.is_not_distinct_from(
                    payload.get("kakao_category_group_code")
                ),
                CategoryMappingRule.kakao_category_pattern.is_not_distinct_from(
                    payload.get("kakao_category_pattern")
                ),
                CategoryMappingRule.keyword_pattern.is_not_distinct_from(
                    payload.get("keyword_pattern")
                ),
                CategoryMappingRule.internal_tag == payload["internal_tag"],
            ]
            session.execute(delete(CategoryMappingRule).where(and_(*match_clauses)))

            session.add(CategoryMappingRule(**payload))
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
        "--json",
        dest="json_path",
        type=Path,
        default=DEFAULT_JSON,
        help=f"JSON path (default: {DEFAULT_JSON})",
    )
    args = parser.parse_args()
    count = load_category_mapping(args.json_path)
    print(f"[ok] loaded {count} category_mapping_rule rows from {args.json_path}")


if __name__ == "__main__":
    main()
