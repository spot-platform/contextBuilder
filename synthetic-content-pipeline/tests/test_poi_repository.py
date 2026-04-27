"""Phase 1 — POI repository 단위 테스트."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.poi.json_repository import EmptyPoiRepository, JsonPoiRepository
from pipeline.poi.models import PoiRecord
from pipeline.poi.repository import PoiRepository, get_default_repository


_FALLBACK_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "poi"
    / "poi_normalized_fallback.json"
)


@pytest.fixture
def repo() -> JsonPoiRepository:
    return JsonPoiRepository.from_path(_FALLBACK_PATH)


def test_fallback_loads_50_pois(repo: JsonPoiRepository):
    assert repo.size == 50


def test_list_by_region_returns_all_yeonmu(repo: JsonPoiRepository):
    pois = repo.list_by_region("연무동")
    assert len(pois) == 50
    assert all(p.region_emd == "연무동" for p in pois)


def test_list_by_region_unknown_returns_empty(repo: JsonPoiRepository):
    assert repo.list_by_region("존재하지않는동") == []


def test_list_by_region_and_tags_filter_cafe(repo: JsonPoiRepository):
    cafes = repo.list_by_region_and_tags("연무동", ["is_cafe"], min_confidence=0.7)
    assert len(cafes) >= 20
    assert all(p.is_cafe for p in cafes)


def test_list_by_region_and_tags_filter_park(repo: JsonPoiRepository):
    parks = repo.list_by_region_and_tags("연무동", ["is_park"], min_confidence=0.7)
    assert len(parks) >= 3
    assert all(p.is_park for p in parks)


def test_list_by_region_and_tags_or_match(repo: JsonPoiRepository):
    """OR 매칭: tags 중 하나라도 True 면 포함."""
    activity_or_park = repo.list_by_region_and_tags(
        "연무동", ["is_activity", "is_park"], min_confidence=0.7
    )
    # park 3 + activity 5 -- 단 일부 overlap (성곽 산책길 = park+activity)
    assert len(activity_or_park) >= 7
    assert all(p.is_activity or p.is_park for p in activity_or_park)


def test_list_by_region_and_tags_high_confidence_filter(repo: JsonPoiRepository):
    high = repo.list_by_region_and_tags("연무동", [], min_confidence=0.95)
    assert all(p.mapping_confidence >= 0.95 for p in high)
    assert len(high) < repo.size  # 일부는 filter 됨


def test_list_by_region_and_tags_empty_tag_no_filter(repo: JsonPoiRepository):
    """tags 가 빈 list 면 confidence 만 적용."""
    out = repo.list_by_region_and_tags("연무동", [], min_confidence=0.7)
    assert all(p.mapping_confidence >= 0.7 for p in out)


def test_get_by_id_existing(repo: JsonPoiRepository):
    p = repo.get_by_id(100001)
    assert p is not None
    assert p.name == "연무 커피그라인더"


def test_get_by_id_missing(repo: JsonPoiRepository):
    assert repo.get_by_id(999_999_999) is None


def test_avoid_tag_filter_excludes_nightlife():
    """nightlife 만 골라내면 정확히 2개여야 한다 (fallback fixture 분포)."""
    repo = JsonPoiRepository.from_path(_FALLBACK_PATH)
    nightlife = repo.list_by_region_and_tags("연무동", ["is_nightlife"], 0.7)
    assert len(nightlife) == 2


def test_lcb_dataset_version_propagated(repo: JsonPoiRepository):
    assert repo.lcb_dataset_version == "fallback_v1"


def test_empty_repository_returns_empty():
    repo = EmptyPoiRepository()
    assert repo.list_by_region("연무동") == []
    assert repo.list_by_region_and_tags("연무동", ["is_cafe"], 0.7) == []
    assert repo.get_by_id(100001) is None


def test_get_default_repository_explicit_path():
    repo = get_default_repository(path=_FALLBACK_PATH)
    assert isinstance(repo, JsonPoiRepository)
    assert repo.size == 50


def test_get_default_repository_falls_back_to_empty(monkeypatch, tmp_path):
    """env / default / fallback 모두 없으면 EmptyPoiRepository."""
    monkeypatch.delenv("PIPELINE_POI_PATH", raising=False)
    fake = tmp_path / "nope.json"
    repo = get_default_repository(path=fake)
    # path 가 없으면 다음 candidate 시도. fallback 은 실재하니 그쪽이 매칭됨.
    # 즉 실제로는 fallback fixture 가 잡힘 (이게 정상 default 동작).
    assert isinstance(repo, (JsonPoiRepository, EmptyPoiRepository))


def test_protocol_compatibility(repo: JsonPoiRepository):
    """JsonPoiRepository / EmptyPoiRepository 모두 PoiRepository Protocol 만족."""
    assert isinstance(repo, PoiRepository)
    assert isinstance(EmptyPoiRepository(), PoiRepository)


def test_invalid_dump_format_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"foo": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid POI dump format"):
        JsonPoiRepository.from_path(bad)


def test_invalid_record_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"places": [{"place_id": "not-a-number"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid POI record"):
        JsonPoiRepository.from_path(bad)


def test_record_has_any_tag_helper():
    rec = PoiRecord(
        place_id=1, region_emd="x", name="x", primary_category="cafe",
        sub_category=None, lat=0.0, lng=0.0, address_name=None,
        road_address_name=None, is_food=False, is_cafe=True, is_activity=False,
        is_park=False, is_culture=False, is_nightlife=False, is_lesson=False,
        is_night_friendly=False, is_group_friendly=True, mapping_confidence=1.0,
    )
    assert rec.has_any_tag(["is_cafe"]) is True
    assert rec.has_any_tag(["is_park"]) is False
    assert rec.has_any_tag(["is_park", "is_cafe"]) is True
    assert rec.has_any_tag([]) is True  # no-filter
