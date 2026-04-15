"""Layer 4 — LLM Critic Validation (샘플링).

synthetic_content_pipeline_plan.md §5 Layer 4 + §10 비용 관리.

설계 요약
--------
- Layer 1~3 통과 후, 전체의 10~20% 만 critic 평가 대상으로 샘플링.
- 샘플 선정 기준 3 종 (§10-1):
    1. 새 카테고리/지역 조합 — 'new_category_region'
    2. Layer 1~3 경계값 (warnings / retry_count > 0) — 'boundary_score'
    3. 랜덤 10% (policy.random_rate) — 'random_10pct'
- critic 호출은 ``pipeline.llm.codex_client.call_codex`` 를 경유한다 (브리지 독점).
- 공용 16 변수 표준을 사용하지 않는다. critic 전용 5 변수만 주입:
    content_type, content_payload, content_spec_summary, eval_focus, sample_reason
- 호출 실패 / schema error / placeholder 시 deterministic fallback 결과 반환:
    모든 score=0.85, reject=False, fallback=True.
- Layer 6 scoring 은 이 결과가 ``None`` 일 수도 있고 (샘플링 제외),
  ``fallback=True`` 일 수도 있다 (샘플링되었지만 LLM 실패). 두 경우 모두 처리 가능.
"""
from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from pipeline import metrics
from pipeline.spec.models import ContentSpec
from pipeline.validators.types import Rejection, ValidationResult

log = logging.getLogger(__name__)

__all__ = [
    "CriticResult",
    "CriticSamplingPolicy",
    "load_critic_sampling_policy",
    "should_sample_critic",
    "evaluate_critic",
    "critic_to_rejections",
]


# ---------------------------------------------------------------------------
# 경로 상수
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CRITIC_SCHEMA_PATH = _REPO_ROOT / "src" / "pipeline" / "llm" / "schemas" / "critic.json"
_CRITIC_POLICY_PATH = (
    _REPO_ROOT / "config" / "weights" / "critic_sampling_policy.json"
)
_CRITIC_TEMPLATE_ID = "critic:v2"


# ---------------------------------------------------------------------------
# 정책 로더
# ---------------------------------------------------------------------------


@dataclass
class CriticSamplingPolicy:
    """§10 샘플링 정책."""

    random_rate: float = 0.10
    target_overall_rate: float = 0.15

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CriticSamplingPolicy":
        return cls(
            random_rate=float(data.get("random_rate", 0.10)),
            target_overall_rate=float(data.get("target_overall_rate", 0.15)),
        )


def load_critic_sampling_policy(
    path: Optional[Path] = None,
) -> CriticSamplingPolicy:
    """config/weights/critic_sampling_policy.json 로드. 파일이 없으면 기본값."""
    p = path or _CRITIC_POLICY_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CriticSamplingPolicy()
    return CriticSamplingPolicy.from_dict(data)


# ---------------------------------------------------------------------------
# 결과 데이터 클래스
# ---------------------------------------------------------------------------


@dataclass
class CriticResult:
    """Layer 4 LLM critic 평가 결과.

    Attributes:
        naturalness_score / consistency_score / regional_fit_score /
        persona_fit_score / safety_score / peer_tone_score: 0.0~1.0.
        reject: critic 이 판정한 reject 여부.
        reasons: reject 사유 (0~5).
        sampled: critic 평가가 실제로 수행됐는지 여부.
        sample_reason: 샘플링 사유 (new_category_region / boundary_score /
            random_10pct / ''(미샘플링)).
        fallback: 호출 실패로 deterministic 기본값을 채웠는지 여부.

    Phase Peer-E 확장 — peer_tone_score 추가. 톤 매트릭스 준수도 (0~1).
    critic.json schema 의 required 필드와 1:1 일치.
    """

    naturalness_score: float = 0.85
    consistency_score: float = 0.85
    regional_fit_score: float = 0.85
    persona_fit_score: float = 0.85
    safety_score: float = 0.95
    peer_tone_score: float = 0.85
    reject: bool = False
    reasons: List[str] = field(default_factory=list)
    sampled: bool = True
    sample_reason: str = ""
    fallback: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "naturalness_score": self.naturalness_score,
            "consistency_score": self.consistency_score,
            "regional_fit_score": self.regional_fit_score,
            "persona_fit_score": self.persona_fit_score,
            "safety_score": self.safety_score,
            "peer_tone_score": self.peer_tone_score,
            "reject": self.reject,
            "reasons": list(self.reasons),
            "sampled": self.sampled,
            "sample_reason": self.sample_reason,
            "fallback": self.fallback,
        }

    @classmethod
    def deterministic_default(cls, *, sample_reason: str = "") -> "CriticResult":
        """LLM 실패 / 샘플 제외 시 쓸 결정론적 기본값."""
        return cls(
            naturalness_score=0.85,
            consistency_score=0.85,
            regional_fit_score=0.85,
            persona_fit_score=0.85,
            safety_score=0.95,
            peer_tone_score=0.85,
            reject=False,
            reasons=[],
            sampled=False,
            sample_reason=sample_reason,
            fallback=True,
        )


# ---------------------------------------------------------------------------
# 샘플링 결정
# ---------------------------------------------------------------------------


def should_sample_critic(
    spot_id: str,
    content_type: str,
    layer123_result: Optional[ValidationResult],
    batch_stats: Mapping[str, Any],
    rng: Optional[random.Random] = None,
    policy: Optional[CriticSamplingPolicy] = None,
) -> Tuple[bool, str]:
    """§10 critic 샘플링 결정.

    Returns
    -------
    (sampled, reason)
        ``sampled`` True 면 critic 호출 대상. ``reason`` 은 3 종 중 하나:
        'new_category_region' / 'boundary_score' / 'random_10pct'. 미샘플이면 ''.
    """
    if policy is None:
        policy = load_critic_sampling_policy()
    if rng is None:
        rng = random.Random()

    # 1. 새 카테고리/지역 조합
    seen_combos = batch_stats.get("seen_category_region", set()) if batch_stats else set()
    category = batch_stats.get("category") if batch_stats else None
    region = batch_stats.get("region") if batch_stats else None
    if category and region:
        combo = f"{category}|{region}"
        if combo not in seen_combos:
            return True, "new_category_region"

    # 2. 경계값: layer123 의 warning 이 있거나 retry_count > 0
    if layer123_result is not None:
        try:
            warnings = list(layer123_result.warnings)
        except Exception:
            warnings = []
        if warnings:
            return True, "boundary_score"
    retry_count = int(batch_stats.get("retry_count", 0)) if batch_stats else 0
    if retry_count > 0:
        return True, "boundary_score"

    # 3. 랜덤 샘플
    if rng.random() < policy.random_rate:
        return True, "random_10pct"

    return False, ""


# ---------------------------------------------------------------------------
# critic 호출
# ---------------------------------------------------------------------------


def _summarize_spec(spec: ContentSpec) -> str:
    """ContentSpec 을 critic 프롬프트에 삽입할 짧은 요약 문자열로."""
    host = spec.host_persona
    return (
        f"spot_id={spec.spot_id} | region={spec.region} | category={spec.category} | "
        f"host={host.type}({host.tone}) | "
        f"participants={spec.participants.expected_count} | "
        f"schedule={spec.schedule.date} {spec.schedule.start_time} "
        f"({spec.schedule.duration_minutes}min) | "
        f"budget={spec.budget.expected_cost_per_person}원 "
        f"(band {spec.budget.price_band}) | "
        f"indoor={spec.activity_constraints.indoor} "
        f"beginner={spec.activity_constraints.beginner_friendly} "
        f"supporter_required={spec.activity_constraints.supporter_required}"
    )


def _payload_to_text(payload: Mapping[str, Any]) -> str:
    """LLM 에 넣을 payload JSON 문자열 (내부 sentinel 제거)."""
    cleaned: Dict[str, Any] = {
        k: v for k, v in payload.items() if not str(k).startswith("_")
    }
    return json.dumps(cleaned, ensure_ascii=False, indent=2)


def _resolve_critic_model(model: Optional[str]) -> str:
    """critic 모델 해상도. 빈 문자열이면 codex CLI default 사용."""
    if model:
        return model
    return os.environ.get("SCP_CODEX_MODEL_CRITIC", "")


def evaluate_critic(
    spot_id: str,
    content_type: str,
    payload: Mapping[str, Any],
    spec: ContentSpec,
    *,
    sample_reason: str = "random_10pct",
    eval_focus: Optional[str] = None,
    model: Optional[str] = None,
) -> CriticResult:
    """Layer 4 critic 평가 — codex-bridge 경유.

    호출 실패(모듈 부재 / 네트워크 / schema 위반) 시 deterministic fallback 결과를
    반환한다. 반환값은 항상 ``CriticResult`` 인스턴스 (예외 전파 없음).
    """
    # metrics — critic 호출 1 건 기록 (실제 LLM 가지 않아도 샘플링 대상이면 1 count)
    try:
        metrics.record_call("critic", content_type)
    except Exception:  # pragma: no cover — metrics 는 절대 예외 던지면 안 됨
        log.exception("metrics.record_call(critic) failed")

    variables: Dict[str, Any] = {
        "content_type": content_type,
        "content_payload": _payload_to_text(payload),
        "content_spec_summary": _summarize_spec(spec),
        "eval_focus": eval_focus or "",
        "sample_reason": sample_reason,
    }

    try:
        from pipeline.llm.codex_client import call_codex  # type: ignore
    except Exception as exc:  # pragma: no cover
        log.warning(
            "critic: codex_client import failed (%s) — deterministic fallback",
            exc,
        )
        return CriticResult.deterministic_default(sample_reason=sample_reason)

    resolved_model = _resolve_critic_model(model)

    try:
        response = call_codex(
            template_id=_CRITIC_TEMPLATE_ID,
            variables=variables,
            schema_path=_CRITIC_SCHEMA_PATH,
            model=resolved_model,
            previous_rejections=None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "critic: call_codex(%s/%s) failed: %s — deterministic fallback",
            spot_id,
            content_type,
            exc,
        )
        return CriticResult.deterministic_default(sample_reason=sample_reason)

    if not isinstance(response, Mapping):
        log.warning("critic: response not a mapping — fallback")
        return CriticResult.deterministic_default(sample_reason=sample_reason)

    # schema 는 codex-bridge 가 강제하지만, stub/placeholder 대응으로 방어적 파싱.
    try:
        naturalness = float(response.get("naturalness_score", 0.85))
        consistency = float(response.get("consistency_score", 0.85))
        regional = float(response.get("regional_fit_score", 0.85))
        persona = float(response.get("persona_fit_score", 0.85))
        safety = float(response.get("safety_score", 0.95))
        # Phase Peer-E 신규 — peer_tone_score. fallback 0.85.
        peer_tone = float(response.get("peer_tone_score", 0.85))
        reject = bool(response.get("reject", False))
        reasons_raw = response.get("reasons") or []
        reasons = [str(r) for r in reasons_raw][:5]
    except (TypeError, ValueError) as exc:
        log.warning("critic: response parse failed (%s) — fallback", exc)
        return CriticResult.deterministic_default(sample_reason=sample_reason)

    return CriticResult(
        naturalness_score=_clip01(naturalness),
        consistency_score=_clip01(consistency),
        regional_fit_score=_clip01(regional),
        persona_fit_score=_clip01(persona),
        safety_score=_clip01(safety),
        peer_tone_score=_clip01(peer_tone),
        reject=reject,
        reasons=reasons,
        sampled=True,
        sample_reason=sample_reason,
        fallback=False,
    )


def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


# ---------------------------------------------------------------------------
# critic → Rejection 변환 헬퍼
# ---------------------------------------------------------------------------


def critic_to_rejections(critic: CriticResult) -> List[Rejection]:
    """CriticResult 를 loop 가 소비하는 ``Rejection`` 리스트로 변환.

    - reject=True 이면 severity="reject" 하나 이상 생성.
    - reject=False 이지만 reasons 가 있으면 severity="warn" 로 기록.
    - fallback=True 는 warn 하나 더 추가 (logging 용).
    """
    rejections: List[Rejection] = []
    if critic.reject:
        if not critic.reasons:
            rejections.append(
                Rejection(
                    layer="critic",
                    rejected_field="__critic__",
                    reason="critic_reject",
                    detail="critic 이 reject=true 를 반환했지만 reasons 가 비어있음",
                    instruction="critic 기준(safety/자연스러움/일관성)을 다시 확인해 재생성",
                    severity="reject",
                )
            )
        for i, reason in enumerate(critic.reasons):
            rejections.append(
                Rejection(
                    layer="critic",
                    rejected_field=f"__critic__[{i}]",
                    reason="critic_reject",
                    detail=reason,
                    instruction="critic 이 지적한 부분을 수정하여 재생성",
                    severity="reject",
                )
            )
    else:
        for i, reason in enumerate(critic.reasons):
            rejections.append(
                Rejection(
                    layer="critic",
                    rejected_field=f"__critic__[{i}]",
                    reason="critic_warn",
                    detail=reason,
                    instruction="선택적 개선 힌트 (점수 감점만 반영)",
                    severity="warn",
                )
            )
    if critic.fallback:
        rejections.append(
            Rejection(
                layer="critic",
                rejected_field="__critic_fallback__",
                reason="critic_fallback",
                detail="critic LLM 호출 실패로 deterministic 기본값을 사용했습니다.",
                instruction="LLM 경로를 점검하세요 (점수에는 영향 없음).",
                severity="warn",
            )
        )
    return rejections
