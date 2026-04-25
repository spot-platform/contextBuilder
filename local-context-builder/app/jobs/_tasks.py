"""Explicit Celery task registry.

``app/celery_app.py`` also uses ``autodiscover_tasks(["app.jobs"])``,
but we register every task here by name so ``admin`` endpoints can
call ``celery.send_task("jobs.<name>")`` without relying on import-
side-effect magic.

**Task names are load-bearing**: they must match the ``send_task``
calls inside ``app/api/admin.py``. Renaming a task here without
updating the API is a QA-blocking drift.
"""

from __future__ import annotations

from typing import Any

from app.celery_app import celery
from app.jobs import (
    bootstrap_regions,
    build_all_features,
    full_rebuild,
    incremental_refresh,
    merge_real_data,
    publish_dataset,
    recompute_attractiveness,
)


@celery.task(name="jobs.bootstrap_regions")
def task_bootstrap_regions() -> dict[str, Any]:
    return bootstrap_regions.run_bootstrap()


@celery.task(name="jobs.full_rebuild")
def task_full_rebuild(target_city: str) -> dict[str, Any]:
    return full_rebuild.run_full_rebuild(target_city)


@celery.task(name="jobs.incremental_refresh")
def task_incremental_refresh(
    target_city: str, force: bool = False
) -> dict[str, Any]:
    return incremental_refresh.run_incremental_refresh(target_city, force=force)


@celery.task(name="jobs.build_all_features")
def task_build_all_features(
    target_city: str, dataset_version: str | None = None
) -> dict[str, Any]:
    return build_all_features.run(target_city, dataset_version)


@celery.task(name="jobs.publish_dataset")
def task_publish_dataset(
    target_city: str, dataset_version: str, build_type: str = "full"
) -> dict[str, Any]:
    return publish_dataset.run(target_city, dataset_version, build_type=build_type)


@celery.task(name="jobs.merge_real_data")
def task_merge_real_data(target_city: str) -> dict[str, Any]:
    return merge_real_data.run(target_city)


@celery.task(
    name="jobs.recompute_attractiveness",
    # FE handoff 2026-04-24: FE 는 rate limit 을 분당 3회로 제한한다.
    # Celery 단에서도 동일 feed_id 가 동시에 2 개 이상 큐에 쌓이지 않도록
    # ``rate_limit`` 으로 안전망을 둔다 (라우터 실패 대비).
    rate_limit="3/m",
)
def task_recompute_attractiveness(feed_id: str) -> dict[str, Any]:
    return recompute_attractiveness.run_recompute_attractiveness(feed_id)


#: All task names this service exposes. Used by ``admin`` unit tests
#: as a single source of truth when checking send_task wiring.
REGISTERED_TASK_NAMES: tuple[str, ...] = (
    "jobs.bootstrap_regions",
    "jobs.full_rebuild",
    "jobs.incremental_refresh",
    "jobs.build_all_features",
    "jobs.publish_dataset",
    "jobs.merge_real_data",
    "jobs.recompute_attractiveness",
)
