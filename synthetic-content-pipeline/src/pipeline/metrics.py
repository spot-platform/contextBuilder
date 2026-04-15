"""§14 지표 측정용 thread-local 카운터.

pipeline-qa 가 스팟당 LLM 호출 수 / 소요 시간 / 재시도 횟수를 측정하기 위한
가장 얇은 thread-local 래퍼. 외부 모듈은 아래 4 개 함수만 사용한다:

- ``start_spot(spot_id)`` — 스팟 처리 진입 시 호출.
- ``record_call(category, sub="")`` — 각 LLM 호출(generation / critic) 앞뒤에서 호출.
- ``record_retry()`` — retry 발생 시 1 회 호출.
- ``end_spot()`` — 스팟 처리 종료 시 호출. 측정 dict 반환.

※ generator 내부의 ``generate_with_retry`` 는 호출 계수를 직접 잡지 못한다.
  generator 측은 이미 Phase 2 에 publish 된 ``base.py`` 를 유지해야 하므로
  (loop 가 수정 금지 대상), loop 는 "후보 수 × 예상 호출 수" 로 근사한다.
  정확한 호출 수는 phase3_delta.md 의 caveat 항목에서 다룬다.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict

__all__ = [
    "start_spot",
    "record_call",
    "record_retry",
    "end_spot",
    "snapshot",
]


_state = threading.local()


def _ensure_state() -> None:
    if not hasattr(_state, "spot_id"):
        start_spot("__anon__")


def start_spot(spot_id: str) -> None:
    """스팟 처리 시작. 이전 값이 있으면 덮어쓴다."""
    _state.spot_id = spot_id
    _state.t0 = time.time()
    _state.calls = {"generation": 0, "critic": 0, "total": 0}
    _state.retries = 0


def record_call(category: str, sub: str = "") -> None:
    """LLM 호출 1 건 기록.

    Parameters
    ----------
    category : str
        ``"generation"`` 또는 ``"critic"`` 등.
    sub : str
        sub-category (예: content_type). 현재는 카운트에만 쓰지 않고 인터페이스
        호환을 위해 받아만 둔다.
    """
    _ensure_state()
    _state.calls[category] = _state.calls.get(category, 0) + 1
    _state.calls["total"] = _state.calls.get("total", 0) + 1


def record_retry() -> None:
    """재시도 1 건 기록."""
    _ensure_state()
    _state.retries = getattr(_state, "retries", 0) + 1


def snapshot() -> Dict[str, Any]:
    """현재 값을 dict 로 반환 (end_spot 없이 중간 확인용)."""
    _ensure_state()
    return {
        "spot_id": getattr(_state, "spot_id", None),
        "elapsed_seconds": round(time.time() - _state.t0, 3),
        "llm_calls": dict(_state.calls),
        "retry_count": getattr(_state, "retries", 0),
    }


def end_spot() -> Dict[str, Any]:
    """스팟 처리 종료. 최종 측정 dict 를 반환한다."""
    _ensure_state()
    result = {
        "spot_id": getattr(_state, "spot_id", None),
        "elapsed_seconds": round(time.time() - _state.t0, 3),
        "llm_calls": dict(_state.calls),
        "retry_count": getattr(_state, "retries", 0),
    }
    # 상태는 다음 start_spot 에서 재초기화되므로 여기선 그대로 둔다.
    return result
