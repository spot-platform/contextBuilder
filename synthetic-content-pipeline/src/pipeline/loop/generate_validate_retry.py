"""§6 생성-검증-재시도 루프 오케스트레이션.

흐름 (플랜 §6):

    Generate candidate × 2 (MVP)            ← generators/base.py (이미 retry 내장)
        ↓
    Schema validate                          ← Layer 1 (dispatch.run_individual)
        ↓
    Rule validate                            ← Layer 2 (dispatch.run_individual)
        ↓
    Cross-reference validate (스팟 단위)     ← Layer 3 (dispatch.run_cross_reference)
        ↓
    Diversity check (배치 단위)              ← Layer 5 (diversity.compute_diversity)
        ↓
    Critic evaluate (샘플링)                 ← Layer 4 (critic.evaluate_critic)
        ↓
    Score & rank                             ← Layer 6 (scoring.compute_quality_score)
        ↓
    if best_score < 0.65:
        재생성 (rejection feedback 포함, 최대 2회)   ← generators 내부 retry
    elif best_score < 0.80:
        critic 리뷰 후 판정                           ← conditional
    else:
        승인                                          ← approved

주의:
    - generators/base.py 내부가 이미 Layer 1+2 를 quick_validator 로 쓰는
      rejection feedback 재시도 (``generate_with_retry``) 를 수행한다.
      따라서 loop 는 그 결과(Candidate 2 개)를 입력으로 받아 Layer 3~6 만 조립.
    - Layer 3 실패 시 loop 가 해당 content type 1 회 재생성.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Type

from pipeline import metrics
from pipeline.generators.base import Candidate
from pipeline.spec.models import ContentSpec
from pipeline.validators.critic import (
    CriticResult,
    evaluate_critic,
    load_critic_sampling_policy,
    should_sample_critic,
)
from pipeline.validators.diversity import compute_diversity
from pipeline.validators.dispatch import run_cross_reference, run_individual
from pipeline.validators.scoring import (
    CONDITIONAL_THRESHOLD,
    classify,
    compute_quality_score,
)
from pipeline.validators.types import ValidationResult

log = logging.getLogger(__name__)

__all__ = [
    "ContentProcessResult",
    "SpotProcessResult",
    "GENERATOR_FACTORIES",
    "process_single_content",
    "process_spot_full",
]


# ---------------------------------------------------------------------------
# 결과 dataclass
# ---------------------------------------------------------------------------


@dataclass
class ContentProcessResult:
    """단일 content type 처리 결과."""

    spot_id: str
    content_type: str
    selected_candidate: Optional[Candidate]
    quality_score: float
    classification: str
    critic_used: bool
    critic_sample_reason: str
    layer_results: Dict[str, Any] = field(default_factory=dict)
    candidates_meta: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spot_id": self.spot_id,
            "content_type": self.content_type,
            "selected_variant": (
                self.selected_candidate.variant if self.selected_candidate else None
            ),
            "payload": (
                self.selected_candidate.payload if self.selected_candidate else None
            ),
            "quality_score": self.quality_score,
            "classification": self.classification,
            "critic_used": self.critic_used,
            "critic_sample_reason": self.critic_sample_reason,
            "layer_results": self.layer_results,
            "candidates_meta": list(self.candidates_meta),
        }


@dataclass
class SpotProcessResult:
    """스팟 전체 5 type 처리 결과."""

    spot_id: str
    contents: Dict[str, ContentProcessResult] = field(default_factory=dict)
    cross_ref_result: Optional[ValidationResult] = None
    llm_calls_total: int = 0
    elapsed_seconds: float = 0.0
    retry_count_total: int = 0
    approved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spot_id": self.spot_id,
            "contents": {k: v.to_dict() for k, v in self.contents.items()},
            "cross_ref_result": (
                self.cross_ref_result.to_dict() if self.cross_ref_result else None
            ),
            "llm_calls_total": self.llm_calls_total,
            "elapsed_seconds": self.elapsed_seconds,
            "retry_count_total": self.retry_count_total,
            "approved": self.approved,
        }


# ---------------------------------------------------------------------------
# GENERATOR_FACTORIES (lazy import)
# ---------------------------------------------------------------------------


def _lazy_feed() -> Type:
    from pipeline.generators.feed import FeedGenerator  # noqa: WPS433

    return FeedGenerator


def _lazy_detail() -> Type:
    from pipeline.generators.detail import SpotDetailGenerator  # noqa: WPS433

    return SpotDetailGenerator


def _lazy_plan() -> Type:
    from pipeline.generators.plan import SpotPlanGenerator  # noqa: WPS433

    return SpotPlanGenerator


def _lazy_messages() -> Type:
    from pipeline.generators.messages import MessagesGenerator  # noqa: WPS433

    return MessagesGenerator


def _lazy_review() -> Type:
    from pipeline.generators.review import ReviewGenerator  # noqa: WPS433

    return ReviewGenerator


GENERATOR_FACTORIES: Dict[str, Callable[[], Type]] = {
    "feed": _lazy_feed,
    "detail": _lazy_detail,
    "plan": _lazy_plan,
    "messages": _lazy_messages,
    "review": _lazy_review,
}


# ---------------------------------------------------------------------------
# 단일 content 처리
# ---------------------------------------------------------------------------


def _merge_individual(
    content_type: str,
    candidate: Candidate,
    spec: ContentSpec,
) -> ValidationResult:
    """후보 1 개에 대해 Layer 1+2 만 실행 (generator 측이 이미 재시도 후 통과 시킨 payload 를 다시 검증)."""
    try:
        return run_individual(content_type, candidate.payload, spec)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "run_individual(%s) raised %s — treating as ok w/ empty rejections",
            content_type,
            exc,
        )
        return ValidationResult(ok=True, layer="rule", rejections=[], meta={"error": str(exc)})


def process_single_content(
    spot_id: str,
    content_type: str,
    spec: ContentSpec,
    generator_factory: Any,
    *,
    batch_stats: Optional[Mapping[str, Any]] = None,
    approved_cache: Sequence[Any] = (),
    rng: Optional[random.Random] = None,
) -> ContentProcessResult:
    """Layer 1~6 전체를 돌리고 최고점 후보를 선택한다.

    Parameters
    ----------
    generator_factory : class | callable | instance
        ``FeedGenerator`` 같은 클래스 자체, 또는 instance. 필요 시 호출해
        generator 인스턴스를 얻는다.
    batch_stats : Mapping
        critic 샘플링 결정용. ``seen_category_region``, ``category``, ``region``,
        ``retry_count`` 키를 읽는다. 없으면 빈 dict.
    approved_cache : Sequence
        diversity 비교용 기존 승인 콘텐츠. 후보와 동형(Candidate 또는 payload dict).
    """
    # metrics 기록용 — spot 단위 start 는 상위에서.
    metrics.record_call("generation", content_type)

    # generator 인스턴스화
    if isinstance(generator_factory, type):
        generator = generator_factory()
    elif callable(generator_factory):
        try:
            generator = generator_factory()
        except TypeError:
            generator = generator_factory  # already an instance
    else:
        generator = generator_factory

    # 1. 후보 2 개 생성 (내부에서 이미 retry 수행됨)
    try:
        candidates: List[Candidate] = list(generator.generate(spec))
    except Exception as exc:  # noqa: BLE001
        log.error("generator.generate(%s/%s) failed: %s", spot_id, content_type, exc)
        return ContentProcessResult(
            spot_id=spot_id,
            content_type=content_type,
            selected_candidate=None,
            quality_score=0.0,
            classification="rejected",
            critic_used=False,
            critic_sample_reason="",
            layer_results={"error": str(exc)},
            candidates_meta=[],
        )

    # retry_count 집계
    retry_total = 0
    for c in candidates:
        meta = c.meta or {}
        if meta.get("retry_count"):
            retry_total += int(meta["retry_count"])
    if retry_total:
        for _ in range(retry_total):
            metrics.record_retry()

    # 2. Layer 1+2 재검증 (단일 번들 뷰로)
    individual_results: List[ValidationResult] = []
    for c in candidates:
        individual_results.append(_merge_individual(content_type, c, spec))

    # batch_stats 에 retry_count 주입 (샘플링 결정 힌트)
    effective_stats: Dict[str, Any] = dict(batch_stats or {})
    effective_stats.setdefault("category", getattr(spec, "category", None))
    effective_stats.setdefault("region", getattr(spec, "region", None))
    effective_stats.setdefault("retry_count", retry_total)

    # 3. Layer 5 diversity (배치 = 후보 2 개 + 기존 승인 캐시)
    diversity_scores = compute_diversity(
        candidates, content_type, approved_cache
    )

    # 4. Layer 4 critic 샘플링 결정 + 평가 (필요 시)
    policy = load_critic_sampling_policy()
    _rng = rng or random.Random()
    # sampling 은 후보별이 아니라 content 단위로 결정 (§10).
    # 경계값 여부는 첫 번째 후보의 Layer 1+2 warnings 를 기준으로.
    layer123_hint = individual_results[0] if individual_results else None
    sampled, sample_reason = should_sample_critic(
        spot_id,
        content_type,
        layer123_hint,
        effective_stats,
        _rng,
        policy,
    )

    critic_result: Optional[CriticResult] = None
    if sampled:
        # 대표로 primary 후보를 critic 에 태운다.
        target_candidate = candidates[0] if candidates else None
        if target_candidate is not None:
            critic_result = evaluate_critic(
                spot_id,
                content_type,
                target_candidate.payload,
                spec,
                sample_reason=sample_reason,
            )

    # 5. 각 후보 × compute_quality_score
    best_idx = -1
    best_score = -1.0
    best_breakdown: Dict[str, Any] = {}
    candidates_meta: List[Dict[str, Any]] = []
    for i, c in enumerate(candidates):
        cand_id = None
        if c.meta and c.meta.get("seed_hash"):
            cand_id = str(c.meta["seed_hash"])
        div_score = None
        if cand_id and cand_id in diversity_scores:
            div_score = diversity_scores[cand_id]
        else:
            # fallback: 후보 순서 기반으로 매핑
            vals = list(diversity_scores.values())
            div_score = vals[i] if i < len(vals) else 0.85
        layer123 = individual_results[i] if i < len(individual_results) else None
        score, breakdown = compute_quality_score(critic_result, layer123, float(div_score))
        candidates_meta.append(
            {
                "variant": c.variant,
                "seed_hash": c.meta.get("seed_hash") if c.meta else None,
                "retry_count": c.meta.get("retry_count", 0) if c.meta else 0,
                "retry_exhausted": (
                    c.meta.get("retry_exhausted", False) if c.meta else False
                ),
                "diversity_score": div_score,
                "quality_score": score,
                "classification": breakdown["classification"],
                "stub": c.meta.get("stub", False) if c.meta else False,
            }
        )
        if score > best_score:
            best_score = score
            best_idx = i
            best_breakdown = breakdown

    selected = candidates[best_idx] if 0 <= best_idx < len(candidates) else None
    classification = (
        best_breakdown.get("classification") if best_breakdown else "rejected"
    )

    # layer_results 에 layer 정보 요약
    layer_results: Dict[str, Any] = {
        "layer1_2": individual_results[best_idx].to_dict() if 0 <= best_idx < len(individual_results) else None,
        "diversity": diversity_scores,
        "critic": critic_result.to_dict() if critic_result else None,
        "score_breakdown": best_breakdown,
    }

    return ContentProcessResult(
        spot_id=spot_id,
        content_type=content_type,
        selected_candidate=selected,
        quality_score=float(best_score) if best_score >= 0 else 0.0,
        classification=classification or "rejected",
        critic_used=critic_result is not None and not critic_result.fallback,
        critic_sample_reason=sample_reason,
        layer_results=layer_results,
        candidates_meta=candidates_meta,
    )


# ---------------------------------------------------------------------------
# 스팟 전체 처리
# ---------------------------------------------------------------------------


def process_spot_full(
    spot_id: str,
    spec: ContentSpec,
    *,
    approved_cache: Optional[Mapping[str, Sequence[Any]]] = None,
    rng: Optional[random.Random] = None,
) -> SpotProcessResult:
    """스팟 하나에 대해 5 type 전체를 순차 처리한 뒤 cross-reference 검증."""
    metrics.start_spot(spot_id)
    _rng = rng or random.Random()
    approved_cache = approved_cache or {}

    result = SpotProcessResult(spot_id=spot_id)

    batch_stats: Dict[str, Any] = {
        "seen_category_region": set(),
        "category": getattr(spec, "category", None),
        "region": getattr(spec, "region", None),
    }

    processing_order = ("feed", "detail", "plan", "messages", "review")

    for content_type in processing_order:
        factory_getter = GENERATOR_FACTORIES.get(content_type)
        if factory_getter is None:
            log.warning("no generator factory for %s", content_type)
            continue
        try:
            factory_cls = factory_getter()
        except ImportError as exc:
            log.warning(
                "lazy import for %s failed: %s — skipping", content_type, exc
            )
            continue

        cache_for_type = approved_cache.get(content_type, ())
        cpr = process_single_content(
            spot_id,
            content_type,
            spec,
            factory_cls,
            batch_stats=batch_stats,
            approved_cache=cache_for_type,
            rng=_rng,
        )
        result.contents[content_type] = cpr

        # seen_category_region 업데이트 (이후 type 의 샘플링 결정에 반영)
        combo = f"{batch_stats.get('category')}|{batch_stats.get('region')}"
        batch_stats["seen_category_region"].add(combo)

    # Cross-reference (Layer 3) — 5 type bundle
    spot_bundle: Dict[str, Any] = {}
    for ct, cpr in result.contents.items():
        if cpr.selected_candidate is not None:
            spot_bundle[ct] = cpr.selected_candidate.payload

    try:
        cross_ref_result = run_cross_reference(spot_bundle, spec)
    except Exception as exc:  # noqa: BLE001
        log.warning("run_cross_reference(%s) failed: %s", spot_id, exc)
        cross_ref_result = ValidationResult(
            ok=True,
            layer="cross_ref",
            rejections=[],
            meta={"error": str(exc)},
        )
    result.cross_ref_result = cross_ref_result

    # Cross-reference 실패 → 모순 type 1 회 재생성
    if not cross_ref_result.ok:
        failing_types = _failing_content_types(cross_ref_result)
        for ct in failing_types:
            if ct not in GENERATOR_FACTORIES:
                continue
            log.info("cross-ref retry for %s/%s", spot_id, ct)
            factory_cls = GENERATOR_FACTORIES[ct]()
            cpr = process_single_content(
                spot_id,
                ct,
                spec,
                factory_cls,
                batch_stats=batch_stats,
                approved_cache=approved_cache.get(ct, ()),
                rng=_rng,
            )
            result.contents[ct] = cpr
            metrics.record_retry()

        # 재검증 (한 번만)
        spot_bundle_2: Dict[str, Any] = {}
        for ct, cpr in result.contents.items():
            if cpr.selected_candidate is not None:
                spot_bundle_2[ct] = cpr.selected_candidate.payload
        try:
            cross_ref_result = run_cross_reference(spot_bundle_2, spec)
        except Exception as exc:  # noqa: BLE001
            log.warning("run_cross_reference retry(%s) failed: %s", spot_id, exc)
        result.cross_ref_result = cross_ref_result

    # metrics 마무리
    snap = metrics.end_spot()
    result.elapsed_seconds = float(snap.get("elapsed_seconds", 0.0))
    result.llm_calls_total = int(snap.get("llm_calls", {}).get("total", 0))
    result.retry_count_total = int(snap.get("retry_count", 0))

    # 최종 승인 여부
    all_ok_per_type = all(
        cpr.classification in ("approved", "conditional")
        for cpr in result.contents.values()
    )
    result.approved = bool(all_ok_per_type and cross_ref_result.ok)
    return result


def _failing_content_types(cross_ref_result: ValidationResult) -> List[str]:
    """cross_reference rejection 에서 어느 content type 을 재생성해야 하는지 추출.

    rejection.rejected_field 는 ``"<content_type>:<field>"`` 형식이므로 앞 토큰만 꺼낸다.
    """
    types: List[str] = []
    seen = set()
    for r in cross_ref_result.hard_rejections:
        field = r.rejected_field or ""
        if ":" in field:
            ct = field.split(":", 1)[0]
            if ct and ct not in seen:
                seen.add(ct)
                types.append(ct)
    return types
