"""Lightweight metrics helpers.

MVP keeps metrics in-process and emits them as structured log records
so Loki/Cloudwatch can index them. A real Prometheus exporter is a
v1.1 concern; this module keeps the surface small so callers don't
need to care whether metrics are shipped anywhere.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("app.metrics")


def record_collection_stats(
    region_code: str,
    count: int,
    duration_seconds: float,
    *,
    search_type: str | None = None,
    batch_id: str | None = None,
) -> None:
    """Record how many places a collector run processed for a region."""

    logger.info(
        "collection region=%s count=%d duration=%.2fs search_type=%s batch_id=%s",
        region_code,
        count,
        duration_seconds,
        search_type or "-",
        batch_id or "-",
    )


def record_pipeline_step(
    step: str,
    dataset_version: str,
    duration_seconds: float,
    **extra: Any,
) -> None:
    """Record the wall-clock duration of a processor step."""

    tail = " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
    logger.info(
        "pipeline step=%s dataset_version=%s duration=%.2fs %s",
        step,
        dataset_version,
        duration_seconds,
        tail,
    )


@contextmanager
def timed(step: str, **extra: Any) -> Iterator[dict[str, float]]:
    """Context manager that logs the wall-clock duration of a block.

    Yields a dict into which the caller may stash extra metadata; the
    dict is emitted with the final log line.
    """

    start = time.perf_counter()
    bucket: dict[str, float] = {}
    try:
        yield bucket
    finally:
        elapsed = time.perf_counter() - start
        merged = {**extra, **bucket}
        tail = " ".join(f"{k}={v}" for k, v in merged.items()) if merged else ""
        logger.info("timed step=%s duration=%.2fs %s", step, elapsed, tail)
