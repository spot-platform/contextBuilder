"""POI integration package — POI-anchored content pipeline §Phase 1.

local-context-builder 가 publish 한 ``place_normalized`` dump 를 read-only
로 로드하여 routing 알고리즘과 generator 에 실제 POI 좌표 / 이름 / 카테고리
를 공급한다.

핵심 모듈:
- ``models.PoiRecord`` — 19 필드 frozen dataclass.
- ``repository.PoiRepository`` — Protocol.
- ``json_repository.JsonPoiRepository`` — dump JSON 기반 구현.

LCB 코드는 한 줄도 import 하지 않는다. dump 는 ``scripts/import_lcb_poi_dump.py``
가 raw SQL 로 LCB DB 에서 추출하여 ``data/poi/poi_normalized_v*.json`` 에 저장.
"""
from pipeline.poi.models import PoiRecord
from pipeline.poi.repository import PoiRepository, get_default_repository

__all__ = [
    "PoiRecord",
    "PoiRepository",
    "get_default_repository",
]
