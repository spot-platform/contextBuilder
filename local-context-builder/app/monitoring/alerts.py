"""Alerting hooks — Slack webhook stub.

The full observability stack (PagerDuty, Grafana alertmanager) is a
v1.1 concern. For MVP we expose a single :func:`send_slack` entry
point. If the ``SLACK_WEBHOOK_URL`` env var is unset (local dev /
test) the function logs a warning and returns ``False`` instead of
raising, so batch jobs never fail just because alerting is off.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def send_slack(message: str, *, channel: str | None = None, **extra: Any) -> bool:
    """POST ``message`` to the configured Slack incoming webhook.

    Returns ``True`` on success, ``False`` if no webhook is configured
    or the HTTP call fails. Never raises — alerting must be fire-and-
    forget from the caller's perspective.
    """

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        logger.warning(
            "send_slack: SLACK_WEBHOOK_URL not set, skipping. message=%r", message
        )
        return False

    payload: dict[str, Any] = {"text": message}
    if channel:
        payload["channel"] = channel
    if extra:
        payload["attachments"] = [{"fields": [
            {"title": k, "value": str(v), "short": True} for k, v in extra.items()
        ]}]

    try:
        import httpx  # local import keeps cold-start cheap

        resp = httpx.post(webhook, json=payload, timeout=5.0)
        if resp.status_code >= 400:
            logger.error(
                "send_slack: webhook returned %d body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - alerting must never raise
        logger.exception("send_slack failed: %s", exc)
        return False


def notify_job_failure(job_name: str, error: str, **ctx: Any) -> None:
    """Convenience wrapper used by Celery task failure handlers."""

    send_slack(
        f":rotating_light: job *{job_name}* failed: {error}",
        job=job_name,
        **ctx,
    )


def notify_publish_failed(version_name: str, issues: list[str]) -> None:
    """Dataset publish quality-gate rejection."""

    summary = "\n".join(f"- {i}" for i in issues[:10])
    send_slack(
        f":warning: dataset `{version_name}` publish FAILED\n{summary}",
        dataset_version=version_name,
        issue_count=len(issues),
    )
