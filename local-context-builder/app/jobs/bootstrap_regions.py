"""Bootstrap job — seeds ``region_master`` and ``category_mapping_rule``.

Thin wrapper around the existing seed scripts under ``scripts/``.
Having a ``app.jobs`` entrypoint lets the admin API or Celery call the
bootstrap the same way it invokes other jobs — without having to know
about module-level CLI argparse.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from scripts.load_category_mapping import (
    DEFAULT_JSON as DEFAULT_CATEGORY_JSON,
    load_category_mapping,
)
from scripts.load_region_master import (
    DEFAULT_CSV as DEFAULT_REGION_CSV,
    load_region_master,
)

logger = logging.getLogger(__name__)


def run_bootstrap(
    *,
    region_csv: Path | None = None,
    mapping_json: Path | None = None,
) -> dict[str, Any]:
    """Load both seed files. Safe to re-run; both scripts are idempotent."""

    region_csv = region_csv or DEFAULT_REGION_CSV
    mapping_json = mapping_json or DEFAULT_CATEGORY_JSON

    logger.info("bootstrap_regions: loading region_master from %s", region_csv)
    regions_loaded = load_region_master(region_csv)

    logger.info(
        "bootstrap_regions: loading category_mapping_rule from %s", mapping_json
    )
    mappings_loaded = load_category_mapping(mapping_json)

    return {
        "regions_loaded": regions_loaded,
        "mappings_loaded": mappings_loaded,
        "region_csv": str(region_csv),
        "mapping_json": str(mapping_json),
    }
