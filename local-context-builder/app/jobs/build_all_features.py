"""End-to-end build pipeline (plan §6~§11).

Chains every processor step so a single call rebuilds:

    normalize_places
      → build_region_features
      → (optional) merge_real_data (v1.1 stub)
      → build_persona_region_weights
      → build_spot_weights
      → publish_dataset

Each stage commits its own writes, so a partial failure leaves
previous stages durable but marks the ``dataset_version`` as
``failed`` via the publisher's quality gate.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.db import SessionLocal
from app.jobs import merge_real_data
from app.processors import (
    build_persona_region_weights,
    build_region_features,
    build_spot_weights,
    normalize_places,
)
from app.services import publisher_service

logger = logging.getLogger(__name__)


def _default_version() -> str:
    return f"v_{datetime.utcnow():%Y%m%d}_{uuid4().hex[:6]}"


def run(
    target_city: str,
    dataset_version: str | None = None,
) -> dict[str, Any]:
    """Execute the full pipeline for ``target_city``.

    Returns a summary dict containing the generated ``dataset_version``
    and the per-step outputs.
    """

    version = dataset_version or _default_version()
    summary: dict[str, Any] = {
        "dataset_version": version,
        "target_city": target_city,
    }

    with SessionLocal() as db:
        logger.info(
            "build_all_features: dataset_version=%s target_city=%s start",
            version,
            target_city,
        )

        summary["normalize"] = normalize_places.process_batch(db)
        summary["region_features"] = build_region_features.build(
            db, version, target_city
        )

        # v1.1 stub — no-op when REALSERVICE_DATABASE_URL is unset.
        summary["merge_real_data"] = merge_real_data.run(target_city)

        summary["persona_weights"] = build_persona_region_weights.build(
            db, version, target_city
        )
        summary["spot_weights"] = build_spot_weights.build(
            db, version, target_city
        )

        publish_result = publisher_service.publish(
            db, version, target_city, build_type="full"
        )
        summary["publish_status"] = publish_result.status
        summary["publish_error"] = publish_result.error_message

    logger.info(
        "build_all_features: dataset_version=%s finished status=%s",
        version,
        summary["publish_status"],
    )
    return summary
