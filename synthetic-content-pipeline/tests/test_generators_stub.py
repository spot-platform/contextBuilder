"""test_generators_stub — FeedGenerator smoke (stub 모드)."""
from __future__ import annotations

import json

import pytest

from pipeline.generators.base import COMMON_VARIABLE_KEYS
from pipeline.generators.feed import FeedGenerator
from pipeline.spec.models import ContentSpec


def _load_golden_spec(path):
    with open(path, "r", encoding="utf-8") as fh:
        return ContentSpec.model_validate(json.load(fh))


@pytest.fixture()
def golden_spec(goldens_specs):
    assert goldens_specs, "goldens/specs/*.json 필요"
    # 연무동 food spec 을 기본 smoke 대상으로
    target = next((p for p in goldens_specs if "yeonmu_evening" in p.name), goldens_specs[0])
    return _load_golden_spec(target)


def test_generate_returns_two_candidates(golden_spec):
    gen = FeedGenerator()
    candidates = gen.generate(golden_spec)
    assert len(candidates) == 2
    assert candidates[0].variant == "primary"
    assert candidates[1].variant == "alternative"


def test_spec_to_variables_includes_common_standard(golden_spec):
    """공용 변수 표준 16개 모두 포함."""
    gen = FeedGenerator()
    variables = gen.spec_to_variables(golden_spec, variant="primary", length_bucket="medium")
    missing = COMMON_VARIABLE_KEYS - set(variables.keys())
    assert not missing, f"공용 변수 누락: {missing}"


def test_payload_has_eight_feed_keys(golden_spec):
    """feed schema 필수 8개 키 모두 존재 (stub fixture default.json 기반)."""
    required = {
        "title",
        "summary",
        "tags",
        "price_label",
        "region_label",
        "time_label",
        "status",
        "supporter_label",
    }
    gen = FeedGenerator()
    candidates = gen.generate(golden_spec)
    for c in candidates:
        missing = required - set(c.payload.keys())
        assert not missing, f"payload 누락: {missing} variant={c.variant}"


def test_candidates_meta_has_length_and_seed(golden_spec):
    gen = FeedGenerator()
    candidates = gen.generate(golden_spec)
    for c in candidates:
        assert "length_bucket" in c.meta
        assert c.meta["length_bucket"] in {"short", "medium", "long"}
        assert "seed_hash" in c.meta
