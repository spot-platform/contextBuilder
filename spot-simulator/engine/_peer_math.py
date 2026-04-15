"""Peer engine 공용 헬퍼 — plan §3 Phase B.

`engine/_math.py` 와 분리되어 있는 이유: legacy path 가 import 하는
`engine._math.clamp` 에 peer 전용 헬퍼를 섞지 않아 append-only 원칙을
단단히 유지하기 위함. peer 경로 모듈(`fee.py`, `peer_decision.py`,
`negotiation.py`, `request_lifecycle.py`)만 이 모듈을 import 한다.
"""

from __future__ import annotations

from typing import Mapping


def level_floor_to_teach(skill: str, catalog: Mapping[str, Mapping]) -> int:
    """`skills_catalog.yaml` 의 ``level_floor_to_teach`` 를 읽는다.

    catalog 에 해당 스킬 spec 이 없거나 key 가 빠져 있으면 plan §3-4 기본값
    인 **3** 을 반환. 엔진이 호출 경로마다 ``catalog.get(skill, {}).get(...)``
    를 반복하지 않도록 이 한 함수로 접근한다.
    """

    spec = catalog.get(skill) if catalog else None
    if not spec:
        return 3
    try:
        return int(spec.get("level_floor_to_teach", 3))
    except (TypeError, ValueError):
        return 3


def clamp01(x: float) -> float:
    """``[0, 1]`` 로 clip. peer decision 공식 전용 (legacy `_math.clamp`
    와 동일 동작이지만 peer 모듈 내부 결정성을 위해 별도 심볼)."""

    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x
