"""Recompute attractiveness report for a single feed — FE handoff 2026-04-24.

FE의 ``POST /api/v1/feed/{feed_id}/attractiveness/recompute`` 엔드포인트가
호출하는 Celery task 의 실체. 라우터는 ``celery.send_task(
"jobs.recompute_attractiveness", args=[feed_id])`` 만 호출하고 202 +
job_id 를 즉시 반환; 본 task 가 비동기 실행 후 ``attractiveness_report_cache``
테이블을 upsert 한다.

Rate limit (분당 3회) 는 API 라우터 쪽 책임 — 본 task 는 중복 호출이 와도
안전하도록 **idempotent** 하게 구현한다 (같은 입력 → 같은 결과).

현재 스캐폴드 단계라 실제 feed 시그널 계산 로직은 TODO 로 남긴다. 입력
feed 에 해당하는:
    - signals (8종)
    - your_fee
    - fee_distribution (같은 skill_topic × region 의 p25/p50/p75/p90)
    - improvement_hints (LLM 기반 or rule 기반)
을 모아서 ``attractiveness_service.build_report()`` 로 조립 후 캐시 upsert.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def run_recompute_attractiveness(feed_id: str) -> dict[str, Any]:
    """Idempotent recompute for a single feed.

    반환 dict 는 admin API 가 그대로 노출:
        {
          "feed_id": str,
          "status": "completed" | "failed",
          "composite_score": float | None,
          "verdict": str | None,       # AttractivenessVerdict enum
          "error": str | None,
        }

    실패 시에도 exception 을 올리지 않고 ``status=failed`` + ``error`` 로
    회신해 FE 가 polling 시 항상 dict 를 받는다.
    """

    try:
        # TODO(integration-qa): 실제 signal/fee 수집 연결.
        # 현 시점에선 recompute 파이프라인이 갖춰지지 않아 noop 로 두고,
        # API 계약 (status + job_id) 만 만족시킨다.
        log.info("recompute_attractiveness: feed_id=%s — scaffold noop", feed_id)

        # 캐시 upsert 도 실제 테이블이 아직 없어 보류. 추후 작업 순서:
        #   1) migrations/ 에 attractiveness_report_cache 테이블 추가
        #   2) app/models/attractiveness_report.py ORM 추가
        #   3) 본 함수에서 build_report 호출 후 upsert
        return {
            "feed_id": feed_id,
            "status": "completed",
            "composite_score": None,
            "verdict": None,
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("recompute_attractiveness failed: feed_id=%s", feed_id)
        return {
            "feed_id": feed_id,
            "status": "failed",
            "composite_score": None,
            "verdict": None,
            "error": str(exc),
        }
