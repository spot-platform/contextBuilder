"""test_content_spec_builder — event_log → ContentSpec 경로 검증.

건드리는 파일: pipeline.spec.builder.build_content_spec
"""
from __future__ import annotations

import pytest

from pipeline.spec.builder import build_content_spec
from pipeline.spec.models import ContentSpec


def test_single_spot_builds_valid_spec(event_log_path, region_features_path, sample_spot_ids):
    """첫 spot_id 로 build_content_spec 하면 ContentSpec 반환 + 필수 필드 채워짐."""
    sid = sample_spot_ids[0]
    spec = build_content_spec(
        event_log_path, sid, region_features_path=region_features_path
    )
    assert isinstance(spec, ContentSpec)
    # 필수 필드 None/empty 가 아님
    assert spec.region and spec.region != ""
    assert spec.category and spec.category != ""
    assert spec.host_persona is not None
    assert spec.host_persona.type
    assert spec.participants is not None
    assert spec.participants.expected_count >= 2
    assert spec.schedule is not None
    assert spec.schedule.date
    assert spec.schedule.start_time
    assert spec.schedule.duration_minutes > 0
    assert spec.budget is not None
    assert spec.budget.price_band >= 1
    assert spec.plan_outline and len(spec.plan_outline) >= 3


def test_builder_is_deterministic(event_log_path, region_features_path, sample_spot_ids):
    """동일 spot_id 로 두 번 호출 시 JSON 직렬화가 byte-identical."""
    sid = sample_spot_ids[0]
    a = build_content_spec(event_log_path, sid, region_features_path=region_features_path)
    b = build_content_spec(event_log_path, sid, region_features_path=region_features_path)
    assert a.model_dump_json() == b.model_dump_json()


def test_build_five_spots_all_succeed(event_log_path, region_features_path, sample_spot_ids):
    """5개 spot_id 일괄 생성 — 모두 예외 없이 성공."""
    ids = sample_spot_ids[:5]
    built = []
    for sid in ids:
        spec = build_content_spec(
            event_log_path, sid, region_features_path=region_features_path
        )
        built.append(spec)
    assert len(built) == 5
    assert all(isinstance(s, ContentSpec) for s in built)
    # 각 spec 의 spot_id 가 입력과 일치
    assert [s.spot_id for s in built] == ids


def test_missing_spot_raises(event_log_path, region_features_path):
    """없는 spot_id 는 ValueError."""
    with pytest.raises(ValueError):
        build_content_spec(
            event_log_path, "S_NONEXISTENT_ZZZZ", region_features_path=region_features_path
        )
