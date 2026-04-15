"""Phase 3 — loop/generate_validate_retry.py 단위 테스트 (stub 모드).

`process_single_content` 와 `process_spot_full` 의 핵심 동작:

1. 단일 spot/feed → ContentProcessResult 반환, classification ∈ {approved, conditional, rejected}.
2. 단일 spot 5 종 → 모두 처리, llm_calls_total > 0.
3. metrics start_spot/end_spot 1 회씩, record_call 카운트 증가 동작.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import metrics
from pipeline.generators.feed import FeedGenerator
from pipeline.loop.generate_validate_retry import (
    ContentProcessResult,
    SpotProcessResult,
    process_single_content,
    process_spot_full,
)
from pipeline.spec.models import ContentSpec


_REPO_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN_FOOD = _REPO_ROOT / "data" / "goldens" / "specs" / "golden_food_yeonmu_evening.json"
_GOLDEN_CAFE = _REPO_ROOT / "data" / "goldens" / "specs" / "golden_cafe_sinchon_weekend.json"

_VALID_CLASSIFICATIONS = {"approved", "conditional", "rejected"}


@pytest.fixture()
def golden_spec() -> ContentSpec:
    return ContentSpec.model_validate(json.loads(_GOLDEN_FOOD.read_text(encoding="utf-8")))


@pytest.fixture()
def golden_spec_cafe() -> ContentSpec:
    return ContentSpec.model_validate(json.loads(_GOLDEN_CAFE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 1. process_single_content
# ---------------------------------------------------------------------------


def test_process_single_content_returns_valid_result(golden_spec):
    metrics.start_spot(golden_spec.spot_id)
    result = process_single_content(
        spot_id=golden_spec.spot_id,
        content_type="feed",
        spec=golden_spec,
        generator_factory=FeedGenerator,
    )
    metrics.end_spot()

    assert isinstance(result, ContentProcessResult)
    assert result.spot_id == golden_spec.spot_id
    assert result.content_type == "feed"
    assert result.classification in _VALID_CLASSIFICATIONS
    # 후보가 1 개 이상 candidates_meta 에 잡혀야 한다.
    assert len(result.candidates_meta) >= 1
    for cm in result.candidates_meta:
        assert "quality_score" in cm
        assert "classification" in cm
    # selected_candidate 는 best 후보로 설정되어야 한다 (rejected 라도 선정 가능).
    assert result.selected_candidate is not None or result.classification == "rejected"


# ---------------------------------------------------------------------------
# 2. process_spot_full
# ---------------------------------------------------------------------------


def test_process_spot_full_runs_all_five_types(golden_spec):
    result = process_spot_full(golden_spec.spot_id, golden_spec)

    assert isinstance(result, SpotProcessResult)
    assert result.spot_id == golden_spec.spot_id

    expected_types = {"feed", "detail", "plan", "messages", "review"}
    assert set(result.contents.keys()) == expected_types

    # 모든 content type 의 분류가 유효해야 한다.
    for ct, cpr in result.contents.items():
        assert cpr.classification in _VALID_CLASSIFICATIONS, (
            f"{ct} got {cpr.classification}"
        )

    # llm_calls_total 은 generation 호출 + critic 샘플링으로 ≥ 5 (5 type 최소).
    assert result.llm_calls_total >= 5
    assert result.elapsed_seconds >= 0.0
    # cross_ref 결과가 잡혀야 한다.
    assert result.cross_ref_result is not None


def test_process_spot_full_metrics_tracking(golden_spec_cafe):
    """metrics 가 정상적으로 record_call 카운트를 증가시키는지 확인."""
    # 이전 상태가 leak 되지 않도록 명시적으로 start.
    metrics.start_spot("__pretest__")
    snap_before = metrics.snapshot()
    assert snap_before["llm_calls"]["total"] == 0

    result = process_spot_full(golden_spec_cafe.spot_id, golden_spec_cafe)

    # spot 완료 후 thread-local 은 process_spot_full 내부에서 end_spot 호출.
    # llm_calls_total > 0 임을 결과로 검증.
    assert result.llm_calls_total > 0


def test_process_single_content_metrics_start_end_pair(golden_spec):
    """start_spot/end_spot 가 호출되면 결과 카운트가 누적된다."""
    metrics.start_spot(golden_spec.spot_id)
    process_single_content(
        spot_id=golden_spec.spot_id,
        content_type="feed",
        spec=golden_spec,
        generator_factory=FeedGenerator,
    )
    snap = metrics.end_spot()
    # generation + (샘플링되면 critic) ≥ 1
    assert snap["llm_calls"]["total"] >= 1
    assert snap["llm_calls"]["generation"] >= 1
