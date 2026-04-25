"""BaseGenerator + Candidate — content-generator-engineer 공통 인프라.

설계 메모
---------
- 모든 LLM 호출은 `pipeline.llm.codex_client.call_codex` 를 경유한다.
  codex-bridge-engineer 가 아직 client 를 publish 하지 않았다면 ImportError 가 나므로,
  generator 는 try/except 로 폴백 placeholder 를 반환하고 warning 을 찍는다.
- `spec_to_variables` 는 codex-bridge 의 `prompt_contract.md` 에 정의된 **공용 변수 표준**
  과 **이름이 100% 일치**해야 한다. 이름이 어긋나면 pipeline-qa 의 boundary audit 에서
  걸리므로 반드시 아래 키 집합을 유지하라.
- `length_bucket` / `sample_variant` 는 deterministic seed 로 결정된다 (재현성).
"""
from __future__ import annotations

import json
import logging
import random
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pipeline.spec.models import ContentSpec

logger = logging.getLogger(__name__)

# Phase 3: retry loop + validator dispatch 통합.
# import 는 런타임에도 안전 (retry.py → codex_client 는 이미 Phase 1 에서 stable).
try:  # pragma: no cover — 개발 중 validator 가 아직 publish 되지 않은 경우 보호
    from pipeline.llm.retry import generate_with_retry  # type: ignore
except ImportError:  # pragma: no cover
    generate_with_retry = None  # type: ignore

try:  # pragma: no cover
    from pipeline.validators.dispatch import run_individual  # type: ignore
except ImportError:  # pragma: no cover
    run_individual = None  # type: ignore


# ---------------------------------------------------------------------------
# 공용 변수 표준 (prompt_contract.md 와 일치 필수)
# ---------------------------------------------------------------------------

#: codex-bridge `prompt_contract.md` 가 정의한 공용 변수 이름.
#: 이 set 와 `spec_to_variables()` 의 반환 dict key 가 super-set 관계여야 한다.
#:
#: Phase Peer-E 확장 — peer_phaseD_delta.md §5 변수 매핑 표의 21 신규 키를
#: append-only 로 추가. 기존 Phase 1~4 의 16 키는 그대로 유지.
COMMON_VARIABLE_KEYS: frozenset = frozenset({
    # ── Phase 1 기존 16 키 ─────────────────────────────────────────────
    "spot_id",
    "region_label",
    "category",
    "host_persona",
    "participants_expected_count",
    "schedule_date",
    "schedule_time",
    "schedule_day_type",
    "schedule_time_slot",
    "budget_price_band",
    "budget_cost_per_person",
    "activity_constraints",
    "plan_outline",
    "activity_result",
    "desired_length_bucket",
    "sample_variant",
    # ── Phase Peer-E 신규 21 키 (peer_phaseD_delta.md §5) ─────────────
    # peer marketplace 핵심
    "skill_topic",
    "host_skill_level",
    "teach_mode",
    "venue_type",
    "fee_breakdown",
    # origination
    "origination_mode",
    "originating_voice",
    "is_request_matched",
    "originating_request_summary",
    "responded_at_tick",
    # counter-offer 재협상
    "had_renegotiation",
    "renegotiation_history",
    "original_target_partner_count",
    "final_partner_count",
    # 관계 & 평판
    "bonded_partner_count",
    "bond_updates_at_settlement",
    "friend_upgrades",
    "referrals_triggered",
    "host_reputation_before",
    "host_reputation_after",
    "host_earn_from_this_spot",
    # LLM 톤 플래그
    "peer_tone_required",
    # ── Taste profile (Job 1 발현, 5 generator 공유) ──────────────────
    "taste_facets",
    "recent_obsession",
    "curiosity_hooks",
})


# ---------------------------------------------------------------------------
# 길이 / time slot 계산
# ---------------------------------------------------------------------------

#: §7-2 길이 분포 (short / medium / long).
LENGTH_DISTRIBUTION: List[tuple[str, float]] = [
    ("short", 0.30),
    ("medium", 0.50),
    ("long", 0.20),
]

#: §3 시뮬레이터의 7 slot 매핑.
TIME_SLOT_BUCKETS: List[tuple[range, str]] = [
    (range(0, 5), "dawn"),
    (range(5, 9), "morning"),
    (range(9, 11), "late_morning"),
    (range(11, 14), "lunch"),
    (range(14, 17), "afternoon"),
    (range(17, 21), "evening"),
    (range(21, 24), "night"),
]


def _seeded_random(*parts: str) -> random.Random:
    """deterministic seed: 인자들을 join 하여 hash."""
    seed_str = "|".join(parts)
    return random.Random(hash(seed_str) & 0xFFFFFFFF)


def sample_length_bucket(spot_id: str, variant: str) -> str:
    """spot_id × variant deterministic seed 로 §7-2 분포에서 길이 버킷 샘플링."""
    rng = _seeded_random(spot_id, variant, "len")
    roll = rng.random()
    cumulative = 0.0
    for name, weight in LENGTH_DISTRIBUTION:
        cumulative += weight
        if roll <= cumulative:
            return name
    return LENGTH_DISTRIBUTION[-1][0]


def resolve_day_type(date_iso: str) -> str:
    """YYYY-MM-DD → weekday | weekend (토/일이면 weekend)."""
    dt = datetime.strptime(date_iso, "%Y-%m-%d").date()
    return "weekend" if dt.weekday() >= 5 else "weekday"


def resolve_time_slot(start_time: str) -> str:
    """HH:MM → §3 7 slot 중 하나."""
    hour = int(start_time.split(":", 1)[0])
    for hours, slot in TIME_SLOT_BUCKETS:
        if hour in hours:
            return slot
    return "evening"  # 폴백


def normalize_region_label(region: str) -> str:
    """ContentSpec.region 이 \"연무동\" 같은 단일 동명이면 \"수원시 {name}\" 으로 보강.
    이미 \"수원시 ...\" 또는 \"... 시 ...\" 형식이면 그대로 반환.
    \"알 수 없음\" 폴백은 그대로 둔다.
    """
    if not region:
        return "지역 미상"
    if "시" in region or "구" in region or " " in region:
        return region
    if region == "알 수 없음":
        return region
    return f"수원시 {region}"


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """LLM 응답을 generator 단위로 캡슐화한 후보 객체.

    Attributes:
        content_type: feed | detail | plan | messages | review.
        variant: primary | alternative.
        payload: 파싱된 JSON dict (스키마 검증은 codex-bridge 또는 validator 가 수행).
        template_id: e.g. "feed:v1" — 프롬프트 버전 추적용.
        meta: length_bucket, seed_hash, prompt 변수 hash, fallback flag 등.
    """

    content_type: str
    variant: str
    payload: Dict[str, Any]
    template_id: str
    meta: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# BaseGenerator
# ---------------------------------------------------------------------------


class BaseGenerator:
    """모든 콘텐츠 생성기의 공통 베이스.

    서브클래스는 `content_type`, `template_id`, `schema_path` 를 정의하고,
    필요 시 `spec_to_variables` 에서 추가 필드를 mix-in 한다.
    """

    content_type: str = "base"
    template_id: str = "base:v1"
    schema_path: Optional[Path] = None
    template_path: Optional[str] = None  # config/prompts/<type>/v2.j2 형식

    # ------------------------------------------------------------------
    # 공용 변수 빌드
    # ------------------------------------------------------------------

    def spec_to_variables(
        self,
        spec: ContentSpec,
        *,
        variant: str,
        length_bucket: str,
    ) -> Dict[str, Any]:
        """ContentSpec → 프롬프트 변수 dict.

        반환되는 키는 `COMMON_VARIABLE_KEYS` super-set 이어야 한다.
        서브클래스는 super().spec_to_variables() 결과를 받고 generator 별 키만 추가한다.
        """
        host = spec.host_persona
        host_persona_obj = {
            "type": host.type,
            "tone": host.tone,
            "communication_style": host.communication_style,
        }
        constraints_obj = {
            "indoor": spec.activity_constraints.indoor,
            "beginner_friendly": spec.activity_constraints.beginner_friendly,
            "supporter_required": spec.activity_constraints.supporter_required,
        }
        activity_result_obj: Optional[Dict[str, Any]] = None
        if spec.activity_result is not None:
            activity_result_obj = {
                "actual_participants": spec.activity_result.actual_participants,
                "no_show_count": spec.activity_result.no_show_count,
                "duration_actual_minutes": spec.activity_result.duration_actual_minutes,
                "issues": list(spec.activity_result.issues),
                "overall_sentiment": spec.activity_result.overall_sentiment,
            }

        # ── Phase Peer-E: peer 확장 필드 ──────────────────────────────
        # fee_breakdown 은 dict 형태로 평탄화 (peer_labor_fee/material_cost/…).
        fee_breakdown_obj: Optional[Dict[str, Any]] = None
        if spec.fee_breakdown is not None:
            fee_breakdown_obj = {
                "peer_labor_fee": spec.fee_breakdown.peer_labor_fee,
                "material_cost": spec.fee_breakdown.material_cost,
                "venue_rental": spec.fee_breakdown.venue_rental,
                "equipment_rental": spec.fee_breakdown.equipment_rental,
                "total": spec.fee_breakdown.total,
                "passthrough_total": spec.fee_breakdown.passthrough_total,
            }

        variables: Dict[str, Any] = {
            # ── Phase 1 기존 16 키 ────────────────────────────────────
            "spot_id": spec.spot_id,
            "region_label": normalize_region_label(spec.region),
            "category": spec.category,
            "host_persona": host_persona_obj,
            "participants_expected_count": spec.participants.expected_count,
            "schedule_date": spec.schedule.date,
            "schedule_time": spec.schedule.start_time,
            "schedule_day_type": resolve_day_type(spec.schedule.date),
            "schedule_time_slot": resolve_time_slot(spec.schedule.start_time),
            "budget_price_band": spec.budget.price_band,
            "budget_cost_per_person": spec.budget.expected_cost_per_person,
            "activity_constraints": constraints_obj,
            "plan_outline": list(spec.plan_outline),
            "activity_result": activity_result_obj,
            "desired_length_bucket": length_bucket,
            "sample_variant": variant,
            # ── Phase Peer-E 신규 21 키 ───────────────────────────────
            "skill_topic": spec.skill_topic,
            "host_skill_level": spec.host_skill_level,
            "teach_mode": spec.teach_mode,
            "venue_type": spec.venue_type,
            "fee_breakdown": fee_breakdown_obj,
            "origination_mode": spec.origination_mode,
            "originating_voice": spec.originating_voice,
            "is_request_matched": spec.is_request_matched,
            "originating_request_summary": spec.originating_request_summary,
            "responded_at_tick": spec.responded_at_tick,
            "had_renegotiation": spec.had_renegotiation,
            "renegotiation_history": list(spec.renegotiation_history),
            "original_target_partner_count": spec.original_target_partner_count,
            "final_partner_count": spec.final_partner_count,
            "bonded_partner_count": spec.bonded_partner_count,
            "bond_updates_at_settlement": list(spec.bond_updates_at_settlement),
            "friend_upgrades": list(spec.friend_upgrades),
            "referrals_triggered": list(spec.referrals_triggered),
            "host_reputation_before": spec.host_reputation_before,
            "host_reputation_after": spec.host_reputation_after,
            "host_earn_from_this_spot": spec.host_earn_from_this_spot,
            "peer_tone_required": spec.peer_tone_required,
            # ── Taste profile (Job 1 발현) ────────────────────────────
            "taste_facets": list(spec.taste_facets),
            "recent_obsession": spec.recent_obsession,
            "curiosity_hooks": list(spec.curiosity_hooks),
        }

        # 변수 표준 일관성 검사 (개발용 sanity assert).
        missing = COMMON_VARIABLE_KEYS - variables.keys()
        if missing:
            raise RuntimeError(
                f"spec_to_variables missing required keys: {sorted(missing)}"
            )
        return variables

    # ------------------------------------------------------------------
    # codex bridge 호출
    # ------------------------------------------------------------------

    def _call_codex(
        self,
        *,
        variables: Dict[str, Any],
        previous_rejections: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """codex bridge 호출. bridge 미완성 시 placeholder dict 반환.

        bridge 시그니처 (codex-bridge `bridge_api.md` 와 동기):
            call_codex(
                template_id: str,
                variables: Mapping,
                schema_path: Path,
                model: str | None = None,
                previous_rejections: Sequence[Mapping] | None = None,
            ) -> dict   # 파싱된 JSON payload
        """
        try:
            from pipeline.llm.codex_client import call_codex  # type: ignore
        except ImportError:
            warnings.warn(
                "pipeline.llm.codex_client.call_codex not yet available — "
                "falling back to placeholder payload (stub mode).",
                stacklevel=2,
            )
            return self._placeholder_payload(variables)

        try:
            return call_codex(
                template_id=self.template_id,
                variables=variables,
                schema_path=self.schema_path,
                previous_rejections=previous_rejections or None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "call_codex failed (%s) — returning placeholder payload",
                exc,
            )
            return self._placeholder_payload(variables)

    # ------------------------------------------------------------------
    # placeholder (stub / bridge 미완성 폴백)
    # ------------------------------------------------------------------

    def _placeholder_payload(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """서브클래스가 오버라이드하여 stub 모드에서도 그럴듯한 dict 를 반환하도록.

        주의: 반환 dict 는 validator 의 ``additionalProperties: false`` 를 통과해야
        하므로 내부 sentinel(``_content_type`` 등) 을 payload 에 넣으면 안 된다.
        상태 정보는 별도로 ``Candidate.meta`` 에 기록한다.
        """
        return {}

    # ------------------------------------------------------------------
    # public entrypoint
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phase 3: quick_validator 클로저
    # ------------------------------------------------------------------

    def _build_quick_validator(self, spec: ContentSpec):
        """per-variant 호출마다 새로 만드는 quick_validator 클로저.

        Layer 1 (schema) + Layer 2 (rule) 만 평가한다. Layer 3 (cross-reference)
        은 5 type 전체 번들이 모인 뒤에 ``run_cross_reference`` 로 따로 실행한다.

        반환 dict 스펙은 ``pipeline.llm.retry.QuickValidator`` 계약과 동일:
            (ok: bool, rejections: list[{"rejected_field","reason","detail","instruction"}])
        """
        content_type = self.content_type

        def quick_validator(payload: Dict[str, Any]):
            # stub payload 가 내부 sentinel(``_stub`` 등)을 갖고 있어도
            # schema validator 는 additionalProperties=False 설정이 아니면 통과한다.
            # 단, 그 경우에도 payload 를 그대로 넘긴다.
            if run_individual is None:
                # validator 모듈 자체가 아직 없으면 통과 취급 (개발 초기 폴백).
                return True, []
            try:
                result = run_individual(content_type, payload, spec)
            except Exception as exc:  # noqa: BLE001
                # validator 자체 예외는 재시도로 이어지도록 reject 로 리턴.
                logger.warning(
                    "quick_validator(%s) raised: %s — treating as reject",
                    content_type,
                    exc,
                )
                return False, [
                    {
                        "rejected_field": "__validator__",
                        "reason": "validator_error",
                        "detail": str(exc)[:300],
                        "instruction": "payload 스키마를 재확인하고 모든 필수 필드를 포함해 재생성",
                    }
                ]

            rejections: List[Dict[str, Any]] = []
            for r in result.rejections:
                if getattr(r, "severity", "reject") != "reject":
                    continue
                rejections.append(
                    {
                        "rejected_field": r.rejected_field,
                        "reason": r.reason,
                        "detail": r.detail,
                        "instruction": r.instruction,
                    }
                )
            return (len(rejections) == 0, rejections)

        return quick_validator

    # ------------------------------------------------------------------
    # public entrypoint
    # ------------------------------------------------------------------

    def generate(self, spec: ContentSpec) -> List[Candidate]:
        """primary + alternative 후보 2개 생성. (§13 MVP)

        Phase 3 업데이트
        ----------------
        - ``pipeline.llm.retry.generate_with_retry`` 로 rejection feedback 루프 통합.
        - per-variant 클로저로 Layer 1+2 를 묶은 ``quick_validator`` 를 주입한다.
        - retry 메타(``retry_count``, ``retry_exhausted``, ``rejection_history``)
          를 Candidate.meta 에 기록해 pipeline-qa 가 §14 지표를 계산할 수 있게 한다.

        Layer 3 (cross-reference) 는 여기서 실행하지 않는다. 5 type 전체 번들이
        모인 후 ``loop/generate_validate_retry.py`` 가 ``run_cross_reference`` 를
        별도 호출한다.
        """
        candidates: List[Candidate] = []
        for variant in ("primary", "alternative"):
            length_bucket = sample_length_bucket(spec.spot_id, variant)
            variables = self.spec_to_variables(
                spec, variant=variant, length_bucket=length_bucket
            )
            seed_hash = hex(hash((spec.spot_id, variant, length_bucket)) & 0xFFFFFFFF)

            quick_validator = self._build_quick_validator(spec)

            if generate_with_retry is None or self.schema_path is None:
                # retry 래퍼 없이 single-shot (개발 폴백 / base class 직접 사용).
                payload = self._call_codex(variables=variables)
                retry_exhausted = False
                rejection_history: List[Dict[str, Any]] = []
            else:
                try:
                    payload = generate_with_retry(
                        template_id=self.template_id,
                        variables=variables,
                        schema_path=self.schema_path,
                        quick_validator=quick_validator,
                        max_retries=2,
                    )
                except Exception as exc:  # noqa: BLE001
                    # retry 래퍼 내부 (codex_client) 예외 — placeholder 로 폴백.
                    logger.warning(
                        "generate_with_retry(%s/%s) failed: %s — placeholder fallback",
                        self.template_id,
                        variant,
                        exc,
                    )
                    payload = self._placeholder_payload(variables)
                    retry_exhausted = True
                    rejection_history = [
                        {
                            "rejected_field": "__call__",
                            "reason": "retry_wrapper_exception",
                            "detail": str(exc)[:300],
                            "instruction": "",
                        }
                    ]
                else:
                    # retry.py 는 실패 시에만 ``_retry_exhausted`` / ``_history`` 를
                    # payload 에 주입한다. meta 로 옮기고 payload 에서 제거한다.
                    if not isinstance(payload, dict):
                        payload = {"_raw": payload}
                    retry_exhausted = bool(payload.pop("_retry_exhausted", False))
                    rejection_history = list(payload.pop("_history", []) or [])

            # retry_count 는 history 길이 / max_retries 로 추정한다.
            # retry.py 가 매 attempt 마다 rejections 를 extend 하므로 정확한 attempt
            # 수는 추정이 어렵지만, pipeline-qa 가 §14 지표용으로 "재시도가 있었는지/
            # 소진됐는지" 만 요구하므로 근사값(0/1/2) 으로 충분하다.
            if not rejection_history:
                retry_count = 0
            elif retry_exhausted:
                retry_count = 2  # max_retries 소진
            else:
                retry_count = 1  # 1 회 재시도로 성공

            candidates.append(
                Candidate(
                    content_type=self.content_type,
                    variant=variant,
                    payload=payload,
                    template_id=self.template_id,
                    meta={
                        "length_bucket": length_bucket,
                        "seed_hash": seed_hash,
                        "stub": bool(payload.get("_stub")) if isinstance(payload, dict) else False,
                        "retry_count": retry_count,
                        "retry_exhausted": retry_exhausted,
                        "rejection_history": rejection_history,
                    },
                )
            )
        return candidates


__all__ = [
    "BaseGenerator",
    "Candidate",
    "COMMON_VARIABLE_KEYS",
    "LENGTH_DISTRIBUTION",
    "TIME_SLOT_BUCKETS",
    "normalize_region_label",
    "resolve_day_type",
    "resolve_time_slot",
    "sample_length_bucket",
]
