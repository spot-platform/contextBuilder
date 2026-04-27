"""PoiRepository Protocol + default factory.

Protocol 은 routing / spec_builder / validator 가 의존하는 *최소 인터페이스*.
구현체는 JSON dump (``json_repository.JsonPoiRepository``) 가 default. 미래에
HTTP 기반 (lcb admin API) 또는 raw DB 어댑터를 추가해도 caller 변경 없이
플러그인 가능.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional, Protocol, runtime_checkable

from pipeline.poi.models import PoiRecord

logger = logging.getLogger(__name__)


@runtime_checkable
class PoiRepository(Protocol):
    """POI 조회 read-only 인터페이스."""

    def list_by_region(self, region_emd: str) -> List[PoiRecord]:
        """행정동 한글명으로 모든 POI 반환. mapping_confidence 필터 미적용."""
        ...

    def list_by_region_and_tags(
        self,
        region_emd: str,
        tags: List[str],
        min_confidence: float = 0.7,
    ) -> List[PoiRecord]:
        """``tags`` 의 어느 하나라도 True 이고 mapping_confidence ≥ min_confidence
        인 POI 만. ``tags`` 가 빈 리스트면 confidence 필터만 적용."""
        ...

    def get_by_id(self, place_id: int) -> Optional[PoiRecord]:
        """place_id 로 단건 조회. 없으면 None."""
        ...


# ---------------------------------------------------------------------------
# Default factory
# ---------------------------------------------------------------------------

_DEFAULT_DUMP_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "poi" / "poi_normalized_v1.json"
)
_FALLBACK_DUMP_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "poi"
    / "poi_normalized_fallback.json"
)


def get_default_repository(*, path: Optional[Path] = None) -> PoiRepository:
    """env / default / fallback 우선순위로 JsonPoiRepository 반환.

    1. ``path`` 인자 (테스트용)
    2. ``$PIPELINE_POI_PATH``
    3. ``data/poi/poi_normalized_v1.json``
    4. ``data/poi/poi_normalized_fallback.json``
    5. 셋 다 없으면 빈 in-memory repo (warning + 정상 동작 — Phase 3 builder 가
       region_pool 빔 감지하여 legacy jitter 로 fallback).
    """
    # Late import to avoid module-level cycle (json_repository imports nothing
    # from this module, but keep symmetry).
    from pipeline.poi.json_repository import EmptyPoiRepository, JsonPoiRepository

    candidates: List[Path] = []
    if path is not None:
        candidates.append(path)
    env_path = os.getenv("PIPELINE_POI_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(_DEFAULT_DUMP_PATH)
    candidates.append(_FALLBACK_DUMP_PATH)

    for p in candidates:
        if p.exists():
            return JsonPoiRepository.from_path(p)

    logger.warning(
        "No POI dump found in any of: %s — returning empty repo. "
        "spec_builder will fall back to legacy jitter.",
        [str(c) for c in candidates],
    )
    return EmptyPoiRepository()


__all__ = ["PoiRepository", "get_default_repository"]
