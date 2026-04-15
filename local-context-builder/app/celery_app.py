"""Celery application factory.

Tasks live under ``app/jobs`` and are discovered automatically.
Concrete task registration (routes, schedules, retries) is the
integration-qa agent's responsibility.
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings

_settings = get_settings()

celery = Celery(
    "lcb",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
)

celery.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="Asia/Seoul",
    enable_utc=True,
)

# Discover tasks from app.jobs.*
celery.autodiscover_tasks(["app.jobs"])

# Explicit import for task registration (names consumed by admin API via
# ``celery.send_task``). Importing has side effects — each ``@celery.task``
# declaration in ``_tasks`` attaches to this app instance.
from app.jobs import _tasks as _tasks  # noqa: E402,F401  (side-effect import)
