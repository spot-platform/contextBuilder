"""Rejection-feedback 재시도 래퍼 (Plan §6).

validator-engineer가 Layer 1+2를 묶은 quick_validator 콜백을 전달하면,
거절 사유를 다음 호출의 ``previous_rejections`` 로 주입해 재생성한다.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, List, Mapping, Tuple

from pipeline.llm import codex_client
from pipeline.llm.errors import CodexRateLimitError, CodexSchemaError

log = logging.getLogger(__name__)

QuickValidator = Callable[[dict], Tuple[bool, List[Mapping[str, Any]]]]


def generate_with_retry(
    template_id: str,
    variables: Mapping[str, Any],
    schema_path: Path,
    quick_validator: QuickValidator,
    max_retries: int = 2,
) -> dict:
    """rejection feedback 루프.

    Parameters
    ----------
    template_id, variables, schema_path
        ``codex_client.call_codex`` 와 동일
    quick_validator : Callable[[dict], (bool, list[dict])]
        ``(ok, rejections)`` 반환. ``rejections`` 항목 형식:
        ``{"rejected_field","reason","detail","instruction"}``
    max_retries : int
        rejection 기반 재시도 횟수 (기본 2). 첫 호출 + 재시도 = 최대 ``1+max_retries`` 회

    Returns
    -------
    dict
        최종 응답. 실패 시 마지막 응답에 ``_retry_exhausted=True`` 메타를 덧붙여 반환
    """
    history: List[Mapping[str, Any]] = []
    last_response: dict | None = None
    rate_limit_retried = False

    total_attempts = 1 + max_retries
    for attempt in range(1, total_attempts + 1):
        try:
            response = codex_client.call_codex(
                template_id=template_id,
                variables=variables,
                schema_path=schema_path,
                previous_rejections=list(history) if history else None,
            )
        except CodexSchemaError as e:
            log.warning("attempt %d schema_error: %s", attempt, e)
            history.append(
                {
                    "rejected_field": "__schema__",
                    "reason": "json_parse",
                    "detail": str(e)[:300],
                    "instruction": "JSON Schema에 맞춰 모든 필수 필드를 포함해 재생성",
                }
            )
            last_response = None
            continue
        except CodexRateLimitError as e:
            if rate_limit_retried:
                log.error("rate limit hit twice — giving up")
                raise
            rate_limit_retried = True
            log.warning("rate limit detected, sleeping 30s before retry: %s", e)
            time.sleep(30)
            continue

        last_response = response
        ok, rejections = quick_validator(response)
        if ok:
            return response

        log.info(
            "attempt %d quick-validator rejected (%d issues)",
            attempt,
            len(rejections),
        )
        history.extend(rejections)

    # 재시도 소진. 마지막 응답이 있으면 메타 플래그 부착해 반환.
    if last_response is None:
        # 모든 시도가 schema error 였던 극단 케이스
        return {
            "_retry_exhausted": True,
            "_history": list(history),
        }
    enriched = dict(last_response)
    enriched["_retry_exhausted"] = True
    enriched["_history"] = list(history)
    return enriched
