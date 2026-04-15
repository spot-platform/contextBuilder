"""build_content_spec — event_log.jsonl → ContentSpec dispatcher.

Phase Peer-D 이후 이 모듈은 **얇은 dispatcher** 로 축소되었다. 실제 구현은
두 모듈에 위임한다:

- :mod:`pipeline.spec._legacy` — Phase 1 빌더. CREATE_SPOT 기반 legacy
  event_log 를 읽고 기존 ContentSpec 필드만 채운다. Phase 1~4 pytest 회귀
  보장을 위해 본문을 건드리지 않는다.
- :mod:`pipeline.spec._peer` — Phase Peer-D 빌더. CREATE_TEACH_SPOT 기반
  peer event_log 를 읽고 peer marketplace 확장 필드까지 채운다.

``build_content_spec`` 의 default 는 ``mode="peer"`` 다. 기존 호출자가 mode
인자를 전달하지 않아도 peer event_log 가 입력이면 그대로 동작하며, legacy
event_log (예: ``event_log_legacy_v1.jsonl``) 를 읽어야 하는 경우 명시적으로
``mode="legacy"`` 를 넘긴다.

기존 Phase 1~4 모듈 호환을 위해 legacy 상수/헬퍼 (예: ``SIMULATION_START_DATE``,
``TICKS_PER_DAY``, ``_tick_to_schedule``, ``_infer_category``) 는 이 모듈에서도
re-export 된다. 새 코드는 가능하면 ``pipeline.spec._legacy`` / ``_peer`` 를
직접 import 할 것.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pipeline.spec._legacy import (  # re-export for backward compatibility
    CATEGORY_PLAN_OUTLINE,
    DEFAULT_REGION_FEATURES,
    DENSITY_TO_CATEGORY,
    MINUTES_PER_TICK,
    PERSONA_TYPE_FALLBACK,
    SIMULATION_START_DATE,
    TICKS_PER_DAY,
    _build_budget,
    _collect_spot_events,
    _deterministic_random,
    _infer_category,
    _infer_host_persona,
    _iter_events,
    _load_region_features,
    _resolve_sentiment,
    _summarize_lifecycle,
    _tick_to_schedule,
    build_legacy_content_spec,
)
from pipeline.spec._peer import (
    DEFAULT_SKILLS_CATALOG,
    SKILL_CATEGORY_CLASS,
    build_peer_content_spec,
)
from pipeline.spec.models import ContentSpec


def build_content_spec(
    event_log_path: str | Path,
    spot_id: str,
    *,
    mode: str = "peer",
    region_features_path: Optional[str | Path] = None,
    skills_catalog_path: Optional[str | Path] = None,
) -> ContentSpec:
    """단일 spot 을 ContentSpec 으로 재구성한다.

    Args:
        event_log_path: ``spot-simulator/output/event_log.jsonl`` 경로.
        spot_id: 빌드 대상 spot id (예: ``"S_0001"``).
        mode: ``"peer"`` (default) 또는 ``"legacy"``.
            - ``"peer"``: Phase Peer-D 이후 event_log 포맷. CREATE_TEACH_SPOT
              기반. peer marketplace 확장 필드까지 전부 채움.
            - ``"legacy"``: Phase 1 event_log 포맷. CREATE_SPOT 기반. peer
              확장 필드는 전부 default 값.
        region_features_path: region_features.json 경로. 생략 시
            ``spot-simulator/data/region_features.json``.
        skills_catalog_path: (peer mode 전용) skills_catalog.yaml 경로. 생략 시
            ``spot-simulator/config/skills_catalog.yaml``. fee_breakdown 추정에 사용.

    Returns:
        ContentSpec — Plan §4 스키마. peer mode 에서는 §Phase Peer-D 확장 필드
        까지 채워진다. legacy mode 에서는 확장 필드가 전부 default 값.

    Raises:
        FileNotFoundError: event_log 파일 부재.
        ValueError: 해당 spot_id 의 CREATE_TEACH_SPOT (peer) 또는
            CREATE_SPOT (legacy) 이벤트가 없을 때.
    """
    if mode == "legacy":
        return build_legacy_content_spec(
            event_log_path,
            spot_id,
            region_features_path=region_features_path,
        )
    if mode == "peer":
        return build_peer_content_spec(
            event_log_path,
            spot_id,
            region_features_path=region_features_path,
            skills_catalog_path=skills_catalog_path,
        )
    raise ValueError(f"unknown mode: {mode!r}. expected 'peer' or 'legacy'.")


__all__ = [
    "build_content_spec",
    "build_legacy_content_spec",
    "build_peer_content_spec",
    # re-exported legacy constants/helpers (backward compat)
    "SIMULATION_START_DATE",
    "MINUTES_PER_TICK",
    "TICKS_PER_DAY",
    "DENSITY_TO_CATEGORY",
    "CATEGORY_PLAN_OUTLINE",
    "PERSONA_TYPE_FALLBACK",
    "DEFAULT_REGION_FEATURES",
    "DEFAULT_SKILLS_CATALOG",
    "SKILL_CATEGORY_CLASS",
    "_load_region_features",
    "_iter_events",
    "_collect_spot_events",
    "_deterministic_random",
    "_infer_category",
    "_infer_host_persona",
    "_tick_to_schedule",
    "_build_budget",
    "_resolve_sentiment",
    "_summarize_lifecycle",
]
