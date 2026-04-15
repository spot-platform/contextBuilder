"""Plan §8 STEP 5 — merge real-service activity into ``real_activity_agg``.

This is the **v1.1 stub**: MVP does not require a live real-service DB
connection. The contract is preserved so integration-qa can wire it
into ``build_all_features`` later without refactoring the entry point.

When ``REALSERVICE_DATABASE_URL`` is unset the readonly engine returns
``None`` and we no-op with a clear log line. When the URL *is* present
but the upstream schema is not yet materialized we still fail softly:
the caller treats an empty ``real_activity_agg`` as a sign to fall
back on ``alpha=1, beta=0``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db_readonly import get_readonly_engine

logger = logging.getLogger(__name__)


def run(target_city: str) -> dict[str, Any]:
    """Attempt to populate ``real_activity_agg`` for the given city.

    Returns a summary dict. MVP fields: ``status`` ∈ {"skipped",
    "ok", "error"}, ``message`` a human-readable explanation.
    """

    engine = get_readonly_engine()
    if engine is None:
        logger.info(
            "merge_real_data: readonly engine not configured; skipping "
            "(target_city=%s). alpha/beta will fallback to (1.0, 0.0).",
            target_city,
        )
        return {
            "status": "skipped",
            "message": "REALSERVICE_DATABASE_URL not configured",
            "rows": 0,
        }

    # v1.1 implementation will:
    #   1. open a short transaction with default_transaction_read_only=on
    #   2. select per-region spot/join/completion counts for the last 28d
    #   3. upsert into real_activity_agg keyed by (region_id, window_start, window_end)
    # For MVP we simply return a stub so the pipeline graph stays intact.
    logger.info(
        "merge_real_data: readonly engine present but v1.1 aggregation logic "
        "not yet implemented; skipping aggregation for target_city=%s",
        target_city,
    )
    return {
        "status": "skipped",
        "message": "v1.1 aggregation not implemented",
        "rows": 0,
    }
