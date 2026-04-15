"""Standalone publish job (plan §11 STEP 10).

Exists so an operator can re-run the quality gate on an existing
``dataset_version`` without re-running the upstream pipeline. The
heavy lifting lives in :mod:`app.services.publisher_service`.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db import SessionLocal
from app.services import publisher_service

logger = logging.getLogger(__name__)


def run(
    target_city: str,
    dataset_version: str,
    build_type: str = "full",
) -> dict[str, Any]:
    """Re-publish an already-built ``dataset_version``.

    Returns a dict with ``status`` and ``error_message``.
    """

    if not dataset_version:
        raise ValueError(
            "publish_dataset.run requires dataset_version; pass the "
            "version produced by build_all_features.run"
        )

    with SessionLocal() as db:
        result = publisher_service.publish(
            db, dataset_version, target_city, build_type=build_type
        )
        logger.info(
            "publish_dataset: dataset_version=%s status=%s",
            dataset_version,
            result.status,
        )
        return {
            "dataset_version": dataset_version,
            "status": result.status,
            "error_message": result.error_message,
        }
