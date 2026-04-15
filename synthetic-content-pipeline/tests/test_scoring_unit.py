"""Phase 3 — scoring.py 단위 테스트.

플랜 §5 Layer 6 가중합 / 분류 임계값 / breakdown 구조를 검증한다.
"""
from __future__ import annotations

import math

import pytest

from pipeline.validators.critic import CriticResult
from pipeline.validators.scoring import (
    APPROVED_THRESHOLD,
    CONDITIONAL_THRESHOLD,
    SCORING_WEIGHTS,
    classify,
    compute_quality_score,
)
from pipeline.validators.types import Rejection, ValidationResult


def test_scoring_weights_sum_to_one():
    total = sum(SCORING_WEIGHTS.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9), f"weights sum = {total}"


def test_scoring_weights_keys_match_plan():
    # Phase Peer-E: peer_tone_fit 신규 추가. 합계 1.00 유지.
    expected = {
        "naturalness",
        "consistency",
        "persona_fit",
        "region_fit",
        "business_rule_fit",
        "diversity",
        "peer_tone_fit",
    }
    assert set(SCORING_WEIGHTS.keys()) == expected


def test_classify_thresholds():
    assert classify(0.95) == "approved"
    assert classify(APPROVED_THRESHOLD) == "approved"  # 경계값 포함
    assert classify(APPROVED_THRESHOLD - 1e-6) == "conditional"
    assert classify(0.70) == "conditional"
    assert classify(CONDITIONAL_THRESHOLD) == "conditional"  # 경계값 포함
    assert classify(CONDITIONAL_THRESHOLD - 1e-6) == "rejected"
    assert classify(0.0) == "rejected"


def test_compute_quality_score_with_no_critic_uses_defaults():
    """critic=None 이면 deterministic 기본값으로 ≥ 0.80 이 나와야 한다."""
    layer123 = ValidationResult(ok=True, layer="rule", rejections=[])
    score, breakdown = compute_quality_score(
        critic=None,
        layer123=layer123,
        diversity_score=0.85,
    )
    assert score >= 0.80
    assert breakdown["classification"] == "approved"
    # critic_used False
    assert breakdown["critic_used"] is False


def test_compute_quality_score_with_low_critic_drops_below_065():
    """critic 의 모든 점수가 0.5 이면 quality_score 가 0.65 미만이어야 한다."""
    critic = CriticResult(
        naturalness_score=0.5,
        consistency_score=0.5,
        regional_fit_score=0.5,
        persona_fit_score=0.5,
        safety_score=0.5,
        reject=False,
        reasons=[],
        sampled=True,
        sample_reason="random_10pct",
        fallback=False,
    )
    layer123 = ValidationResult(ok=True, layer="rule", rejections=[])
    score, breakdown = compute_quality_score(
        critic=critic,
        layer123=layer123,
        diversity_score=0.5,
    )
    assert score < 0.65, f"expected <0.65 but got {score}"
    assert breakdown["classification"] == "rejected"


def test_breakdown_has_required_keys():
    layer123 = ValidationResult(ok=True, layer="rule", rejections=[])
    _score, breakdown = compute_quality_score(
        critic=None,
        layer123=layer123,
        diversity_score=0.8,
    )

    components = breakdown.get("components")
    assert components is not None
    # Phase Peer-E: peer_tone_fit 신규 추가. components/weighted 7 키.
    expected_keys = {
        "naturalness",
        "consistency",
        "persona_fit",
        "region_fit",
        "business_rule_fit",
        "diversity",
        "peer_tone_fit",
    }
    assert set(components.keys()) == expected_keys, (
        f"breakdown components missing keys: {expected_keys - set(components.keys())}"
    )

    # weighted dict 도 7 키 동일해야 한다.
    weighted = breakdown.get("weighted")
    assert weighted is not None
    assert set(weighted.keys()) == expected_keys

    # 기본 메타 필드
    for key in ("quality_score", "classification", "critic_used", "warnings_count"):
        assert key in breakdown, f"breakdown missing {key}"


def test_warning_count_lowers_business_rule_fit():
    """warnings 가 많을수록 business_rule_fit 가 낮아져야 한다."""
    layer_clean = ValidationResult(ok=True, layer="rule", rejections=[])
    warn = Rejection(
        layer="rule",
        rejected_field="title",
        reason="length_borderline",
        detail="x",
        instruction="x",
        severity="warn",
    )
    layer_warn = ValidationResult(
        ok=True, layer="rule", rejections=[warn, warn, warn]
    )

    _score_clean, b_clean = compute_quality_score(None, layer_clean, 0.85)
    _score_warn, b_warn = compute_quality_score(None, layer_warn, 0.85)

    brf_clean = b_clean["components"]["business_rule_fit"]
    brf_warn = b_warn["components"]["business_rule_fit"]
    assert brf_warn < brf_clean
