"""ContentSpec pydantic 모델 — synthetic_content_pipeline_plan.md §4 스키마.

Phase Peer-D 확장: peer marketplace 도메인 필드를 **append-only** 로 추가.
기존 Phase 1 필드는 전부 유지되며, 신규 필드는 전부 Optional / default 가 있다.
따라서 legacy path (`mode="legacy"`) 와 peer path (`mode="peer"`) 모두 동일한
ContentSpec 생성자를 통해 생성할 수 있다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, computed_field


class HostPersona(BaseModel):
    """호스트 페르소나 (LLM 톤/스타일 입력)."""

    type: str = Field(..., description="예: supporter_teacher, supporter_neutral")
    tone: str = Field(..., description="자연어 톤 설명")
    communication_style: str = Field(..., description="자연어 커뮤니케이션 스타일")


class Participants(BaseModel):
    """참가자 집계."""

    expected_count: int = Field(..., ge=0)
    persona_mix: List[str] = Field(default_factory=list)


class Schedule(BaseModel):
    """일정 (date/start_time/duration)."""

    date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field(..., description="HH:MM 24시간")
    duration_minutes: int = Field(..., gt=0)


class Budget(BaseModel):
    """예산 정보."""

    price_band: int = Field(..., ge=1, le=5)
    expected_cost_per_person: int = Field(..., ge=0)


class ActivityConstraints(BaseModel):
    """활동 제약."""

    indoor: bool = True
    beginner_friendly: bool = True
    supporter_required: bool = True


class ActivityResult(BaseModel):
    """settle 이후 결과 — 리뷰/메시지 생성기가 참조."""

    actual_participants: int = Field(..., ge=0)
    no_show_count: int = Field(..., ge=0)
    duration_actual_minutes: int = Field(..., ge=0)
    issues: List[str] = Field(default_factory=list)
    overall_sentiment: Literal["positive", "neutral", "negative"] = "neutral"


# ---------------------------------------------------------------------------
# Phase Peer-D 확장 서브모델
# ---------------------------------------------------------------------------


class FeeBreakdownSpec(BaseModel):
    """또래 강사 2층 fee 구조 (peer_labor + passthrough).

    Phase Peer-B 의 ``FeeBreakdown`` dataclass 를 ContentSpec 으로 실어나르기
    위한 in-memory 뷰. simulator event_log 에 원본 dict 가 기록되지 않은
    경우 (Phase Peer-B 현재 상태) 0 / fallback 값이 들어갈 수 있다.

    FE handoff 2026-04-24: 이 스키마는 ``BACKEND_HANDOFF_ENTITIES.md``
    §FeeBreakdown 과 1:1 대응한다. ``total`` / ``passthrough_total`` 은
    Pydantic ``computed_field`` 로 노출해 ``.model_dump()`` 결과에 포함된다.
    """

    peer_labor_fee: int = Field(..., ge=0, description="또래 강사 순마진 (시간/노동)")
    material_cost: int = Field(..., ge=0, description="재료비 실비 (spot 총액)")
    venue_rental: int = Field(..., ge=0, description="장소 대관료 실비 (spot 총액)")
    equipment_rental: int = Field(..., ge=0, description="장비 대여료 실비 (spot 총액)")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        return (
            self.peer_labor_fee
            + self.material_cost
            + self.venue_rental
            + self.equipment_rental
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passthrough_total(self) -> int:
        return self.material_cost + self.venue_rental + self.equipment_rental


class ContentSpec(BaseModel):
    """LLM 입력 스키마 (Plan §4, Phase Peer-D 확장).

    - Phase 1 필드 (spot_id ~ activity_result) 는 그대로 유지.
    - Phase Peer-D 확장 필드는 전부 Optional 또는 default 값을 가진다.
    """

    # ── 기존 Phase 1 필드 ──────────────────────────────────────────────
    spot_id: str
    region: str
    category: str
    spot_type: str = "casual_meetup"
    host_persona: HostPersona
    participants: Participants
    schedule: Schedule
    budget: Budget
    activity_constraints: ActivityConstraints
    plan_outline: List[str]
    activity_result: Optional[ActivityResult] = None

    # ── Phase Peer-D 확장 — peer marketplace 핵심 ─────────────────────
    skill_topic: Optional[str] = Field(
        default=None,
        description='SkillTopic value ("기타", "홈베이킹", ...). peer mode 에서 채움.',
    )
    host_skill_level: Optional[int] = Field(
        default=None, ge=0, le=5, description="호스트 해당 스킬 level 0~5"
    )
    teach_mode: Optional[str] = Field(
        default=None, description='"1:1" | "small_group" | "workshop"'
    )
    venue_type: Optional[str] = Field(
        default=None,
        description=(
            '"cafe" | "home" | "studio" | "park" | "gym" | "online".'
            ' FE handoff 2026-04-24 는 "online" 을 지원하지 않으므로'
            " publish 시 normalize_venue_type_for_publish() 로 5종 enum 중"
            " 하나로 매핑한다."
        ),
    )
    fee_breakdown: Optional[FeeBreakdownSpec] = Field(
        default=None, description="2층 fee (peer_labor + passthrough)"
    )

    # ── origination (offer vs request_matched) ────────────────────────
    origination_mode: str = Field(
        default="offer", description='"offer" | "request_matched"'
    )
    originating_voice: str = Field(
        default="host",
        description='파생: origination_mode 에서 계산. "host" | "learner"',
    )
    originating_request_summary: Optional[str] = Field(
        default=None, description="학생 원 요청 한 줄 요약 (request_matched 전용)"
    )
    responded_at_tick: Optional[int] = Field(
        default=None, description="SUPPORTER_RESPONDED tick (request_matched 전용)"
    )
    is_request_matched: bool = Field(
        default=False, description="편의 플래그 (origination_mode == 'request_matched')"
    )

    # ── counter-offer 재협상 이력 ─────────────────────────────────────
    had_renegotiation: bool = Field(default=False)
    renegotiation_history: List[Dict[str, Any]] = Field(default_factory=list)
    original_target_partner_count: Optional[int] = Field(default=None)
    final_partner_count: Optional[int] = Field(default=None)

    # ── 관계 & 평판 (Phase C 이벤트 파싱) ─────────────────────────────
    bonded_partner_count: int = Field(
        default=0,
        description="이 스팟 settlement 후 host-partner 간 regular 이상 관계 partner 수",
    )
    bond_updates_at_settlement: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="[{partner_id, from, to}, ...]",
    )
    friend_upgrades: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="[{partner_id, skill, sessions}, ...]",
    )
    referrals_triggered: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="[{from, to, host, reason}, ...]",
    )
    host_reputation_before: Optional[float] = Field(default=None)
    host_reputation_after: Optional[float] = Field(default=None)
    host_earn_from_this_spot: Optional[int] = Field(
        default=None, description="POCKET_MONEY_EARNED.amount"
    )

    # ── Geo (MVP 피드 맵용) ───────────────────────────────────────────
    latitude: Optional[float] = Field(
        default=None,
        description="spot 핀 latitude. region center + 시드 jitter (±0.003°).",
    )
    longitude: Optional[float] = Field(
        default=None,
        description="spot 핀 longitude. region center + 시드 jitter (±0.003°).",
    )

    # ── LLM 생성 가이드 ───────────────────────────────────────────────
    peer_tone_required: bool = Field(
        default=True,
        description="Phase E 프롬프트가 이 플래그로 또래 강사 톤을 강제",
    )

    # ── Taste profile (LLM 발현, append-only) ────────────────────────
    # spot 단위로 한 번 LLM 호출(또는 deterministic fallback)로 생성. 5개
    # generator 가 동일한 취향을 참조하도록 spec 에 박아둔다.
    taste_facets: List[str] = Field(
        default_factory=list,
        description="호스트의 세부 취향 키워드 (예: '핑거스타일 카피', '야외 러닝 루트').",
    )
    recent_obsession: Optional[str] = Field(
        default=None,
        description="요즘 빠진 한 가지 — 한 문장.",
    )
    curiosity_hooks: List[str] = Field(
        default_factory=list,
        description="배우고 싶거나 누가 가르쳐줬으면 하는 것 — 짧은 라벨.",
    )


# ---------------------------------------------------------------------------
# FE-facing public enums (2026-04-24 회의 반영)
# ---------------------------------------------------------------------------
#
# BACKEND_HANDOFF_ENTITIES.md §SpotVenueType 은 ``cafe|home|studio|park|gym``
# 5종으로 고정. 시뮬레이터는 ``online`` 도 배출하므로, publish 시점에 5종
# 범주로 매핑한다. 매핑 테이블:
#
#   online  -> studio   (온라인은 "스튜디오 촬영형" 으로 가장 가까움)
#   none    -> cafe     (기본값 fallback)
#   unknown -> cafe     (결측 케이스)

PUBLIC_VENUE_TYPES: tuple[str, ...] = ("cafe", "home", "studio", "park", "gym")

_VENUE_TYPE_REMAP: Dict[str, str] = {
    "online": "studio",
    "none": "cafe",
    "": "cafe",
}


def normalize_venue_type_for_publish(value: Optional[str]) -> str:
    """FE ``SpotVenueType`` enum 5종 중 하나로 정규화한다.

    ContentSpec 빌드 시점에 검증되지 않은 venue_type 이 들어와도,
    publish / SpotCard 조립 시점에서 반드시 이 헬퍼를 거쳐야 FE 계약을
    위반하지 않는다.
    """

    if value is None:
        return "cafe"
    v = value.strip().lower()
    if v in PUBLIC_VENUE_TYPES:
        return v
    return _VENUE_TYPE_REMAP.get(v, "cafe")
