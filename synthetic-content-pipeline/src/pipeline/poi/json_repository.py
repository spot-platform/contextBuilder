"""JSON dump 기반 PoiRepository 구현.

dump 포맷::

    {
      "lcb_dataset_version": "v_2026_04_27",
      "exported_at": "2026-04-27T10:00:00Z",
      "places": [
        {
          "place_id": 12345,
          "region_emd": "연무동",
          "name": "...",
          ... 19 컬럼 ...
        },
        ...
      ]
    }

로드 시 region_emd 별 in-memory index 빌드. ``list_by_region`` / ``list_by_id``
모두 O(1) 조회. min_confidence 필터는 호출 시 적용.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from pipeline.poi.models import PoiRecord

logger = logging.getLogger(__name__)


class JsonPoiRepository:
    """JSON dump 1개 파일을 메모리에 통째로 로드한 read-only repo."""

    def __init__(
        self,
        records: Iterable[PoiRecord],
        *,
        lcb_dataset_version: Optional[str] = None,
        source_path: Optional[Path] = None,
    ) -> None:
        self._by_id: Dict[int, PoiRecord] = {}
        self._by_region: Dict[str, List[PoiRecord]] = {}
        for rec in records:
            self._by_id[rec.place_id] = rec
            self._by_region.setdefault(rec.region_emd, []).append(rec)
        self._lcb_dataset_version = lcb_dataset_version
        self._source_path = source_path

    # ------------------------------------------------------------------
    @classmethod
    def from_path(cls, path: Path) -> "JsonPoiRepository":
        with path.open("r", encoding="utf-8") as fh:
            blob = json.load(fh)

        if not isinstance(blob, dict) or "places" not in blob:
            raise ValueError(
                f"{path}: invalid POI dump format — expected dict with 'places' key"
            )
        places = blob["places"]
        if not isinstance(places, list):
            raise ValueError(f"{path}: 'places' must be a list")

        records = [_record_from_dict(p) for p in places]
        return cls(
            records,
            lcb_dataset_version=blob.get("lcb_dataset_version"),
            source_path=path,
        )

    # ------------------------------------------------------------------
    def list_by_region(self, region_emd: str) -> List[PoiRecord]:
        return list(self._by_region.get(region_emd, ()))

    def list_by_region_and_tags(
        self,
        region_emd: str,
        tags: List[str],
        min_confidence: float = 0.7,
    ) -> List[PoiRecord]:
        pool = self._by_region.get(region_emd, ())
        out: List[PoiRecord] = []
        for rec in pool:
            if rec.mapping_confidence < min_confidence:
                continue
            if tags and not rec.has_any_tag(tags):
                continue
            out.append(rec)
        return out

    def get_by_id(self, place_id: int) -> Optional[PoiRecord]:
        return self._by_id.get(place_id)

    # ------------------------------------------------------------------
    @property
    def lcb_dataset_version(self) -> Optional[str]:
        return self._lcb_dataset_version

    @property
    def source_path(self) -> Optional[Path]:
        return self._source_path

    @property
    def size(self) -> int:
        return len(self._by_id)


# ---------------------------------------------------------------------------
class EmptyPoiRepository:
    """모든 쿼리에 빈 결과 반환. POI dump 가 없을 때 fallback."""

    @property
    def lcb_dataset_version(self) -> Optional[str]:
        return None

    def list_by_region(self, region_emd: str) -> List[PoiRecord]:
        return []

    def list_by_region_and_tags(
        self,
        region_emd: str,
        tags: List[str],
        min_confidence: float = 0.7,
    ) -> List[PoiRecord]:
        return []

    def get_by_id(self, place_id: int) -> Optional[PoiRecord]:
        return None


# ---------------------------------------------------------------------------
def _record_from_dict(d: dict) -> PoiRecord:
    """dump dict → PoiRecord. boolean 필드 default = False."""
    try:
        return PoiRecord(
            place_id=int(d["place_id"]),
            region_emd=str(d["region_emd"]),
            name=str(d["name"]),
            primary_category=str(d["primary_category"]),
            sub_category=d.get("sub_category"),
            lat=float(d["lat"]),
            lng=float(d["lng"]),
            address_name=d.get("address_name"),
            road_address_name=d.get("road_address_name"),
            is_food=bool(d.get("is_food", False)),
            is_cafe=bool(d.get("is_cafe", False)),
            is_activity=bool(d.get("is_activity", False)),
            is_park=bool(d.get("is_park", False)),
            is_culture=bool(d.get("is_culture", False)),
            is_nightlife=bool(d.get("is_nightlife", False)),
            is_lesson=bool(d.get("is_lesson", False)),
            is_night_friendly=bool(d.get("is_night_friendly", False)),
            is_group_friendly=bool(d.get("is_group_friendly", False)),
            mapping_confidence=float(d.get("mapping_confidence", 1.0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid POI record: {d!r} ({exc})") from exc


__all__ = ["JsonPoiRepository", "EmptyPoiRepository"]
