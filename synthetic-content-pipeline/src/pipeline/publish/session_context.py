"""session_context aggregator — FE handoff 2026-04-24 §ConversionHints.

FE의 ``ConversionHintsResponse.session_context`` 필드를 채우는 순수 집계
로직. 입력은 simulator ``event_log.jsonl`` 한 run 전체를 이미 메모리에
올려둔 list[dict]. 출력은 ``BACKEND_HANDOFF_ENTITIES.md §ConversionSessionContext``
스키마의 dict.

BE 서버는 이 함수 결과를 그대로 ``ConversionHintsResponse.data.session_context``
로 실어 보내면 된다 (봉투만 씌우면 됨).

왜 SCP 에 두는가:
    - event_log 의 필드명 계약 (skill_topic, spot_id, closed_at_tick, ...)
      은 ``spot-simulator/models/event.py`` + ``runner.py`` 의 payload
      합의와 1:1 대응. 이 합의가 바뀌면 본 aggregator 도 같이 수정되어야
      하므로, 같은 레포에 두는 게 유지보수 포인트를 줄여준다.
    - 순수 함수이므로 DB/HTTP 접근 없음 → BE 서버가 import 해서 바로 호출.

입력 contract (event dict):
    {
      "event_type": "CREATE_TEACH_SPOT" | "JOIN_TEACH_SPOT" | "SPOT_COMPLETED" | ...,
      "tick": int,
      "spot_id": str,
      "payload": { "skill": str, "closed_at_tick": int | None, ... }
    }

출력 contract:
    {
      "similar_active_count": int,
      "avg_participants": float,
      "typical_lifespan_minutes": int,
      "sample_size": int,
      "scope": "run" | "region" | "global"
    }
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

# FE 가 ms 를 요구하지만 session_context 는 "분" 단위이므로 tick 해상도만
# 알면 된다. 1 tick == time_resolution_hours * 60 분.
DEFAULT_TICK_MINUTES: int = 60

# 모수 부족 시 scope fallback 임계값. 10 개 미만이면 "region" 으로 넓히라는
# 신호를 BE 서버에 보낸다 (scope 을 밝혀 FE 가 신뢰도 UI 를 조정).
_MIN_RUN_SAMPLE: int = 10


@dataclass(frozen=True)
class SessionContext:
    similar_active_count: int
    avg_participants: float
    typical_lifespan_minutes: int
    sample_size: int
    scope: str  # "run" | "region" | "global"

    def as_dict(self) -> dict[str, Any]:
        return {
            "similar_active_count": self.similar_active_count,
            "avg_participants": round(self.avg_participants, 2),
            "typical_lifespan_minutes": self.typical_lifespan_minutes,
            "sample_size": self.sample_size,
            "scope": self.scope,
        }


def _skill_of(event: Mapping[str, Any]) -> Optional[str]:
    payload = event.get("payload") or {}
    return payload.get("skill") or payload.get("skill_topic")


def compute_session_context(
    events: Iterable[Mapping[str, Any]],
    *,
    skill_topic: str,
    tick_minutes: int = DEFAULT_TICK_MINUTES,
    min_run_sample: int = _MIN_RUN_SAMPLE,
) -> SessionContext:
    """run 단위 event_log 를 ``skill_topic`` 으로 필터링해 집계한다.

    - active = 같은 skill_topic 에 대해 ``CREATE_TEACH_SPOT`` 을 발행했고
      아직 close 이벤트(``SPOT_COMPLETED`` / ``SPOT_TIMEOUT`` /
      ``SPOT_DISPUTED`` + settlement) 를 못 받은 스팟 수.
    - avg_participants = 같은 skill_topic 스팟들의 참여자 수 평균
      (``JOIN_TEACH_SPOT`` 이벤트를 spot_id 로 카운트).
    - typical_lifespan_minutes = ``created_at_tick`` → ``closed_at_tick``
      의 median (tick * tick_minutes, 분 단위).

    모수가 ``min_run_sample`` 미만이면 ``scope="region"`` 을 반환해 BE 서버
    쪽에서 region-wide 집계로 fallback 할 신호를 보낸다.
    """

    created_ticks: dict[str, int] = {}   # spot_id -> created_at_tick
    closed_ticks: dict[str, int] = {}    # spot_id -> closed_at_tick
    participants: dict[str, set[str]] = {}  # spot_id -> {persona_id}
    is_skill: set[str] = set()           # spot_id 중 skill 일치

    for ev in events:
        et = ev.get("event_type")
        sid = ev.get("spot_id")
        if not sid:
            continue
        payload = ev.get("payload") or {}

        if et == "CREATE_TEACH_SPOT":
            if _skill_of(ev) == skill_topic:
                is_skill.add(sid)
                created_ticks[sid] = int(payload.get("scheduled_tick", ev.get("tick", 0)))
                created_ticks[sid] = int(ev.get("tick", 0))  # prefer real emit tick
        elif et == "JOIN_TEACH_SPOT":
            if sid in is_skill:
                pid = payload.get("persona_id") or ev.get("agent_id")
                if pid:
                    participants.setdefault(sid, set()).add(str(pid))
        elif et in ("SPOT_COMPLETED", "SPOT_TIMEOUT"):
            if sid in is_skill:
                closed_ticks[sid] = int(
                    payload.get("closed_at_tick", ev.get("tick", 0))
                )

    # similar_active_count: created 됐지만 closed 아직 안 된 것 중 skill 일치.
    active = sum(1 for sid in is_skill if sid not in closed_ticks)

    # avg_participants: 모든 skill 일치 spot 평균 (종료 여부 무관).
    counts = [len(participants.get(sid, set())) for sid in is_skill]
    avg = sum(counts) / len(counts) if counts else 0.0

    # typical_lifespan_minutes: closed 된 것만 median.
    lifespans = []
    for sid, end in closed_ticks.items():
        start = created_ticks.get(sid)
        if start is None:
            continue
        dt = max(0, end - start) * tick_minutes
        lifespans.append(dt)
    median_life = int(statistics.median(lifespans)) if lifespans else 0

    sample_size = len(is_skill)
    scope = "run" if sample_size >= min_run_sample else "region"

    return SessionContext(
        similar_active_count=active,
        avg_participants=avg,
        typical_lifespan_minutes=median_life,
        sample_size=sample_size,
        scope=scope,
    )
