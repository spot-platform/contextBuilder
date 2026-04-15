"""Phase 3 — critic.py 단위 테스트.

`pipeline.validators.critic` 의 4 가지 표면을 검증한다.

1. ``should_sample_critic`` — §10 샘플링 규칙 (3 종 이유)
2. ``evaluate_critic`` — stub 모드에서 default.json 픽스처를 가져와
   파싱하고 ``CriticResult`` 를 돌려주는지
3. ``critic.json`` schema 통과 — default 픽스처가 schema 와 호환되는지
4. ``critic_to_rejections`` — reject / fallback 변환

모든 테스트는 ``stub_env`` autouse 로 SCP_LLM_MODE=stub 가 강제된다.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import jsonschema
import pytest

from pipeline.spec.models import ContentSpec
from pipeline.validators.critic import (
    CriticResult,
    CriticSamplingPolicy,
    critic_to_rejections,
    evaluate_critic,
    load_critic_sampling_policy,
    should_sample_critic,
)
from pipeline.validators.types import Rejection, ValidationResult


_REPO_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN_FOOD = _REPO_ROOT / "data" / "goldens" / "specs" / "golden_food_yeonmu_evening.json"
_CRITIC_SCHEMA_PATH = _REPO_ROOT / "src" / "pipeline" / "llm" / "schemas" / "critic.json"
_CRITIC_DEFAULT_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "codex_stub" / "critic" / "v1" / "default.json"
_CRITIC_REJECT_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "codex_stub" / "critic" / "v1" / "critic_reject_sample.json"


@pytest.fixture()
def golden_spec() -> ContentSpec:
    with _GOLDEN_FOOD.open("r", encoding="utf-8") as fh:
        return ContentSpec.model_validate(json.load(fh))


# ---------------------------------------------------------------------------
# should_sample_critic — 3 종 이유
# ---------------------------------------------------------------------------


class TestShouldSampleCritic:
    def test_new_category_region_combo_returns_true(self):
        """seen_category_region 에 없는 조합은 즉시 True."""
        rng = random.Random(0)
        sampled, reason = should_sample_critic(
            spot_id="S1",
            content_type="feed",
            layer123_result=None,
            batch_stats={
                "seen_category_region": set(),
                "category": "food",
                "region": "수원시 연무동",
                "retry_count": 0,
            },
            rng=rng,
        )
        assert sampled is True
        assert reason == "new_category_region"

    def test_same_combo_no_warnings_no_retry_random_rate_low(self):
        """동일 조합 + warnings 없음 + retry=0 일 때 random rate 가 0.10 이므로
        seed 를 0 으로 고정한 random 시퀀스에서 충분히 많은 호출을 굴려
        대다수가 False 임을 확인한다 (≈ 90% 가 False 면 통과)."""
        seen = {"food|수원시 연무동"}
        layer123 = ValidationResult(ok=True, layer="rule", rejections=[])

        false_count = 0
        total = 200
        rng = random.Random(123)
        for _ in range(total):
            sampled, _reason = should_sample_critic(
                spot_id="S1",
                content_type="feed",
                layer123_result=layer123,
                batch_stats={
                    "seen_category_region": seen,
                    "category": "food",
                    "region": "수원시 연무동",
                    "retry_count": 0,
                },
                rng=rng,
            )
            if not sampled:
                false_count += 1

        # random_rate=0.10 이므로 90% 정도가 False. 통계 노이즈 감안 ≥ 80%.
        assert false_count / total >= 0.80, (
            f"expected ≥80% non-sampled but got {false_count}/{total}"
        )

    def test_warnings_trigger_boundary_score(self):
        """layer123 에 warning rejection 이 있으면 boundary_score."""
        warn = Rejection(
            layer="rule",
            rejected_field="title",
            reason="length_borderline",
            detail="제목 길이가 경계값",
            instruction="조정",
            severity="warn",
        )
        layer123 = ValidationResult(ok=True, layer="rule", rejections=[warn])
        rng = random.Random(0)
        sampled, reason = should_sample_critic(
            spot_id="S1",
            content_type="feed",
            layer123_result=layer123,
            batch_stats={
                "seen_category_region": {"food|수원시 연무동"},
                "category": "food",
                "region": "수원시 연무동",
                "retry_count": 0,
            },
            rng=rng,
        )
        assert sampled is True
        assert reason == "boundary_score"

    def test_retry_count_positive_triggers_boundary_score(self):
        rng = random.Random(0)
        sampled, reason = should_sample_critic(
            spot_id="S1",
            content_type="feed",
            layer123_result=ValidationResult(ok=True, layer="rule", rejections=[]),
            batch_stats={
                "seen_category_region": {"food|수원시 연무동"},
                "category": "food",
                "region": "수원시 연무동",
                "retry_count": 2,
            },
            rng=rng,
        )
        assert sampled is True
        assert reason == "boundary_score"


# ---------------------------------------------------------------------------
# evaluate_critic — stub 모드 default.json 픽스처
# ---------------------------------------------------------------------------


class TestEvaluateCriticStub:
    def test_stub_default_returns_high_scores_no_reject(self, golden_spec):
        result = evaluate_critic(
            spot_id="S1",
            content_type="feed",
            payload={"title": "맛있는 점심 같이해요", "summary": "수원 연무동에서 만나요"},
            spec=golden_spec,
            sample_reason="random_10pct",
        )
        assert isinstance(result, CriticResult)
        # default 픽스처는 모든 점수 ≥ 0.85 / reject False / fallback False
        assert result.reject is False
        assert result.fallback is False
        assert result.naturalness_score >= 0.80
        assert result.consistency_score >= 0.80
        assert result.regional_fit_score >= 0.80
        assert result.persona_fit_score >= 0.80
        assert result.safety_score >= 0.80
        assert result.sampled is True
        assert result.sample_reason == "random_10pct"


# ---------------------------------------------------------------------------
# critic.json schema — default fixture 호환성
# ---------------------------------------------------------------------------


class TestCriticSchema:
    def test_default_fixture_passes_schema(self):
        schema = json.loads(_CRITIC_SCHEMA_PATH.read_text(encoding="utf-8"))
        fixture = json.loads(_CRITIC_DEFAULT_FIXTURE.read_text(encoding="utf-8"))
        jsonschema.validate(fixture, schema)

    def test_reject_fixture_passes_schema(self):
        schema = json.loads(_CRITIC_SCHEMA_PATH.read_text(encoding="utf-8"))
        fixture = json.loads(_CRITIC_REJECT_FIXTURE.read_text(encoding="utf-8"))
        jsonschema.validate(fixture, schema)


# ---------------------------------------------------------------------------
# critic_to_rejections — reject / warn / fallback 분기
# ---------------------------------------------------------------------------


class TestCriticToRejections:
    def test_reject_with_reasons_creates_reject_severity(self):
        critic = CriticResult(
            naturalness_score=0.4,
            consistency_score=0.5,
            regional_fit_score=0.6,
            persona_fit_score=0.5,
            safety_score=0.9,
            reject=True,
            reasons=["톤이 기계적", "지역 정보 부족"],
            sampled=True,
            sample_reason="boundary_score",
            fallback=False,
        )
        rejections = critic_to_rejections(critic)
        assert len(rejections) == 2
        assert all(r.severity == "reject" for r in rejections)
        assert all(r.layer == "critic" for r in rejections)

    def test_no_reject_with_reasons_creates_warn(self):
        critic = CriticResult(
            naturalness_score=0.85,
            consistency_score=0.85,
            regional_fit_score=0.85,
            persona_fit_score=0.85,
            safety_score=0.95,
            reject=False,
            reasons=["미세한 톤 어색함"],
            sampled=True,
            sample_reason="random_10pct",
            fallback=False,
        )
        rejections = critic_to_rejections(critic)
        assert len(rejections) == 1
        assert rejections[0].severity == "warn"

    def test_fallback_adds_warn(self):
        critic = CriticResult.deterministic_default(sample_reason="random_10pct")
        rejections = critic_to_rejections(critic)
        # default 는 reject=False, reasons=[], 단 fallback=True 이므로 warn 1 개.
        assert len(rejections) == 1
        assert rejections[0].severity == "warn"
        assert "critic_fallback" in rejections[0].reason


# ---------------------------------------------------------------------------
# 정책 로더
# ---------------------------------------------------------------------------


def test_policy_loader_defaults_when_path_missing(tmp_path):
    policy = load_critic_sampling_policy(tmp_path / "does_not_exist.json")
    assert isinstance(policy, CriticSamplingPolicy)
    assert 0.0 <= policy.random_rate <= 1.0


def test_policy_loader_reads_repo_config():
    policy = load_critic_sampling_policy()
    assert pytest.approx(policy.random_rate, abs=1e-6) == 0.10
