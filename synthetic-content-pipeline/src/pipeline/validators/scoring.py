"""Layer 6 — Scoring / Ranking.

플랜 §5 Layer 6 + peer pivot plan §5 Phase E — 변경 시 plan 문서와 동기화 필수.

공식 (Phase Peer-E 재조정):

    quality_score =
        0.25 × naturalness
      + 0.20 × consistency
      + 0.15 × persona_fit
      + 0.10 × region_fit
      + 0.05 × business_rule_fit
      + 0.10 × diversity
      + 0.15 × peer_tone_fit   # Phase Peer-E 신규

    합계 = 1.00

승인 기준 (§5 Layer 6 — 유지):

    ≥ 0.80     → "approved"
    0.65~0.79  → "conditional"
    < 0.65     → "rejected"

peer_tone_fit 소스:
- critic.peer_tone_score 사용
- critic=None 이면 deterministic default 0.85
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from pipeline.validators.critic import CriticResult
from pipeline.validators.types import ValidationResult

__all__ = [
    "SCORING_WEIGHTS",
    "APPROVED_THRESHOLD",
    "CONDITIONAL_THRESHOLD",
    "compute_quality_score",
    "classify",
]


# 플랜 §5 Layer 6 + peer pivot plan §5 Phase E — 변경 시 plan 문서와
# config/weights/scoring_weights.json 을 동시에 업데이트해야 한다.
# 순서/이름/값이 100% 일치해야 함. 합계는 반드시 1.00.
SCORING_WEIGHTS: Dict[str, float] = {
    "naturalness": 0.25,
    "consistency": 0.20,
    "persona_fit": 0.15,
    "region_fit": 0.10,
    "business_rule_fit": 0.05,
    "diversity": 0.10,
    "peer_tone_fit": 0.15,  # Phase Peer-E 신규
}

APPROVED_THRESHOLD: float = 0.80
CONDITIONAL_THRESHOLD: float = 0.65

# Layer 2 rule warning 이 이 수를 넘으면 business_rule_fit=0.0 으로 간주.
_MAX_WARNINGS_CEILING: int = 6


def _deterministic_defaults(layer123: Optional[ValidationResult]) -> Dict[str, float]:
    """critic=None 일 때 쓸 결정론적 기본값.

    cross_ref 가 ok 이면 consistency 0.90, 아니면 0.65. 나머지는 plan 하한.
    Phase Peer-E: peer_tone_fit 기본 0.85 (critic 샘플링 제외 시).
    """
    cross_ref_ok = True
    if layer123 is not None:
        try:
            # layer123 이 dispatch 의 merged ValidationResult 인 경우
            meta = getattr(layer123, "meta", {}) or {}
            if meta.get("cross_ref_ok") is False:
                cross_ref_ok = False
            elif not layer123.ok:
                cross_ref_ok = False
        except Exception:
            pass
    return {
        "naturalness": 0.85,
        "consistency": 0.90 if cross_ref_ok else 0.65,
        "persona_fit": 0.85,
        "region_fit": 0.85,
        "peer_tone_fit": 0.85,
    }


def compute_quality_score(
    critic: Optional[CriticResult],
    layer123: Optional[ValidationResult],
    diversity_score: float,
) -> Tuple[float, Dict[str, Any]]:
    """플랜 §5 Layer 6 가중합.

    Parameters
    ----------
    critic : CriticResult | None
        critic 평가 결과. None 이면 결정론적 기본값 사용.
    layer123 : ValidationResult | None
        Layer 1~3 개별 검증 결과 (dispatch.run_individual 반환값 또는
        loop 가 merge 해서 전달). warnings 수로 business_rule_fit 계산.
    diversity_score : float
        Layer 5 결과 (0.0~1.0). 그대로 반영.

    Returns
    -------
    (quality_score, breakdown)
        ``breakdown`` 은 각 컴포넌트 점수, 가중 기여분, 분류를 담은 dict.
    """
    defaults = _deterministic_defaults(layer123)

    if critic is None:
        naturalness = defaults["naturalness"]
        consistency = defaults["consistency"]
        persona_fit = defaults["persona_fit"]
        region_fit = defaults["region_fit"]
        peer_tone_fit = defaults["peer_tone_fit"]
    else:
        naturalness = float(critic.naturalness_score)
        consistency = float(critic.consistency_score)
        persona_fit = float(critic.persona_fit_score)
        region_fit = float(critic.regional_fit_score)
        # Phase Peer-E: critic.peer_tone_score 사용. 누락 시 0.85 fallback.
        peer_tone_fit = float(getattr(critic, "peer_tone_score", 0.85))

    # business_rule_fit: warnings / ceiling → 1.0 - ratio
    if layer123 is not None:
        try:
            n_warn = len(layer123.warnings)
        except Exception:
            n_warn = 0
        if not layer123.ok:
            # hard rejection 이 남아 있는 상태면 business_rule_fit 을 바닥에 붙인다.
            business_rule_fit = 0.0
        else:
            business_rule_fit = max(
                0.0, 1.0 - (n_warn / max(1, _MAX_WARNINGS_CEILING))
            )
    else:
        business_rule_fit = 1.0
        n_warn = 0

    # diversity 그대로
    diversity = float(diversity_score)
    if diversity < 0.0:
        diversity = 0.0
    if diversity > 1.0:
        diversity = 1.0

    score = (
        SCORING_WEIGHTS["naturalness"] * naturalness
        + SCORING_WEIGHTS["consistency"] * consistency
        + SCORING_WEIGHTS["persona_fit"] * persona_fit
        + SCORING_WEIGHTS["region_fit"] * region_fit
        + SCORING_WEIGHTS["business_rule_fit"] * business_rule_fit
        + SCORING_WEIGHTS["diversity"] * diversity
        + SCORING_WEIGHTS["peer_tone_fit"] * peer_tone_fit
    )

    component_values: Dict[str, float] = {
        "naturalness": naturalness,
        "consistency": consistency,
        "persona_fit": persona_fit,
        "region_fit": region_fit,
        "business_rule_fit": business_rule_fit,
        "diversity": diversity,
        "peer_tone_fit": peer_tone_fit,
    }

    breakdown: Dict[str, Any] = {
        "components": dict(component_values),
        "weights": dict(SCORING_WEIGHTS),
        "weighted": {
            k: SCORING_WEIGHTS[k] * component_values[k] for k in SCORING_WEIGHTS
        },
        "quality_score": score,
        "classification": classify(score),
        "critic_used": critic is not None and not critic.fallback,
        "critic_fallback": critic.fallback if critic is not None else False,
        "warnings_count": n_warn,
    }
    return score, breakdown


def classify(score: float) -> str:
    """quality_score → 'approved' | 'conditional' | 'rejected'."""
    if score >= APPROVED_THRESHOLD:
        return "approved"
    if score >= CONDITIONAL_THRESHOLD:
        return "conditional"
    return "rejected"
