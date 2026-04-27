"""PoiRecord — local-context-builder ``place_normalized`` 의 in-memory 표현.

19 필드는 lcb 의 SQLAlchemy 모델 (``app/models/place_normalized.py``) 컬럼과 1:1.
``region_emd`` 는 lcb ``region_master.emd`` 한글 행정동명 (예: "연무동").

scp 의 spec_builder / routing / validator 가 이 dataclass 만 참조한다. lcb
ORM 을 import 하지 않으므로 두 프로젝트는 완전히 격리된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PoiRecord:
    """``place_normalized`` 의 19 컬럼 frozen 표현."""

    place_id: int
    region_emd: str  # "연무동" — region_features.region_name 매칭 키
    name: str
    primary_category: str
    sub_category: Optional[str]
    lat: float
    lng: float
    address_name: Optional[str]
    road_address_name: Optional[str]
    is_food: bool
    is_cafe: bool
    is_activity: bool
    is_park: bool
    is_culture: bool
    is_nightlife: bool
    is_lesson: bool
    is_night_friendly: bool
    is_group_friendly: bool
    mapping_confidence: float

    def has_any_tag(self, tags: list[str]) -> bool:
        """``tags`` 중 하나라도 True 이면 True. 빈 리스트는 항상 True 로 취급
        (no-filter 의 의미)."""
        if not tags:
            return True
        for t in tags:
            if getattr(self, t, False):
                return True
        return False

    def address_for_display(self) -> str:
        """프론트 / LLM 에 보여주기 좋은 주소 한 줄. 도로명 우선."""
        return self.road_address_name or self.address_name or ""


__all__ = ["PoiRecord"]
