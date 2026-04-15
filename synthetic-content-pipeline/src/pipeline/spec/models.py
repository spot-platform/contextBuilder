"""ContentSpec pydantic 모델 — synthetic_content_pipeline_plan.md §4 스키마.

Phase Peer-D 확장: peer marketplace 도메인 필드를 **append-only** 로 추가.
기존 Phase 1 필드는 전부 유지되며, 신규 필드는 전부 Optional / default 가 있다.
따라서 legacy path (`mode="legacy"`) 와 peer path (`mode="peer"`) 모두 동일한
ContentSpec 생성자를 통해 생성할 수 있다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


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
    """

    peer_labor_fee: int = Field(..., ge=0, description="또래 강사 순마진 (시간/노동)")
    material_cost: int = Field(..., ge=0, description="재료비 실비 (spot 총액)")
    venue_rental: int = Field(..., ge=0, description="장소 대관료 실비 (spot 총액)")
    equipment_rental: int = Field(..., ge=0, description="장비 대여료 실비 (spot 총액)")

    @property
    def total(self) -> int:
        return (
            self.peer_labor_fee
            + self.material_cost
            + self.venue_rental
            + self.equipment_rental
        )

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
        description='"cafe" | "home" | "studio" | "park" | "gym" | "online"',
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

    # ── LLM 생성 가이드 ───────────────────────────────────────────────
    peer_tone_required: bool = Field(
        default=True,
        description="Phase E 프롬프트가 이 플래그로 또래 강사 톤을 강제",
    )
