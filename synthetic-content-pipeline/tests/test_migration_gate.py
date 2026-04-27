"""Phase 7 — USE_POI_ANCHORS 환경 토글 + 회귀 안전성 검증."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from pipeline.loop.generate_validate_retry import GENERATOR_FACTORIES


def test_generator_factories_includes_poi_types():
    """price / preparation 이 factory 등록되어 있다."""
    assert "price" in GENERATOR_FACTORIES
    assert "preparation" in GENERATOR_FACTORIES
    # legacy 5종 보존
    for ct in ("feed", "detail", "plan", "messages", "review"):
        assert ct in GENERATOR_FACTORIES


@patch.dict(os.environ, {"USE_POI_ANCHORS": "false"})
def test_use_poi_anchors_disabled_returns_legacy_processing_order():
    """USE_POI_ANCHORS=false 면 processing_order 는 legacy 5종."""
    # process_spot_full 안의 동적 분기를 직접 검증하려면 spec 가 필요.
    # 여기서는 _peer 의 토글 함수만 확인 (간접).
    from pipeline.spec._peer import _is_poi_anchors_enabled
    assert _is_poi_anchors_enabled() is False


@patch.dict(os.environ, {"USE_POI_ANCHORS": "true"})
def test_use_poi_anchors_enabled_default():
    from pipeline.spec._peer import _is_poi_anchors_enabled
    assert _is_poi_anchors_enabled() is True


@patch.dict(os.environ, {}, clear=False)
def test_use_poi_anchors_default_when_unset():
    """환경변수 unset 이면 default True."""
    os.environ.pop("USE_POI_ANCHORS", None)
    from pipeline.spec._peer import _is_poi_anchors_enabled
    assert _is_poi_anchors_enabled() is True


@pytest.mark.parametrize("val", ["false", "FALSE", "0", "no", "off", "False"])
def test_use_poi_anchors_falsey_values(val):
    with patch.dict(os.environ, {"USE_POI_ANCHORS": val}):
        from pipeline.spec._peer import _is_poi_anchors_enabled
        assert _is_poi_anchors_enabled() is False


@pytest.mark.parametrize("val", ["true", "1", "yes", "TRUE", "on", "anything"])
def test_use_poi_anchors_truthy_values(val):
    with patch.dict(os.environ, {"USE_POI_ANCHORS": val}):
        from pipeline.spec._peer import _is_poi_anchors_enabled
        assert _is_poi_anchors_enabled() is True


def test_publisher_tracks_price_preparation_counts():
    """publisher.publish_spot 이 price/preparation 카운트를 반환."""
    from dataclasses import dataclass, field
    from typing import Any, Dict, Optional
    from pipeline.publish.publisher import Publisher

    @dataclass
    class _MockCPR:
        classification: str = "approved"
        selected_candidate: Any = None

    @dataclass
    class _MockSpotResult:
        spot_id: str = "S_X"
        contents: Dict[str, Any] = field(default_factory=dict)
        content_spec: Optional[Any] = None

    class _MockSession:
        def flush(self):
            pass

        def add(self, _row):
            pass

        def execute(self, _stmt):
            class _Result:
                def scalar_one_or_none(self):
                    return None
            return _Result()

    pub = Publisher(_MockSession(), dataset_version="v_test")

    # price/preparation 둘 다 conditional 인 경우
    spot_res = _MockSpotResult(
        contents={
            "price": _MockCPR(classification="approved"),
            "preparation": _MockCPR(classification="rejected"),
        }
    )
    res = pub.publish_spot(spot_res)
    assert res.published_rows.get("price") == 1
    assert res.skipped_rows.get("preparation") == 1


def test_publisher_handles_missing_price_preparation():
    """price/preparation 이 없는 spot 도 publisher 정상 동작 (회귀 안전)."""
    from dataclasses import dataclass, field
    from typing import Any, Dict, Optional
    from pipeline.publish.publisher import Publisher

    @dataclass
    class _MockSpotResult:
        spot_id: str = "S_LEGACY"
        contents: Dict[str, Any] = field(default_factory=dict)
        content_spec: Optional[Any] = None

    class _MockSession:
        def flush(self):
            pass

        def add(self, _row):
            pass

        def execute(self, _stmt):
            class _Result:
                def scalar_one_or_none(self):
                    return None
            return _Result()

    pub = Publisher(_MockSession(), dataset_version="v_test")
    spot_res = _MockSpotResult(contents={})
    res = pub.publish_spot(spot_res)
    # 신규 2종은 0 카운트로 채워져야 함 (key 누락 안 함)
    assert res.published_rows.get("price") == 0
    assert res.published_rows.get("preparation") == 0
    assert res.skipped_rows.get("price") == 0
    assert res.skipped_rows.get("preparation") == 0
