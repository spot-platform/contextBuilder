"""Spot dataclass and SpotStatus enum — plan §2.3 Phase 1 + §3 Phase 2 +
§4 Phase 3 + peer-pivot §2-4 Phase Peer-A.

Phase 2 extends SpotStatus with CONFIRMED / IN_PROGRESS / COMPLETED /
DISPUTED and adds Spot lifecycle timestamps + duration + checked_in/noshow
participant sets.
Phase 3 extends SpotStatus with SETTLED / FORCE_SETTLED and adds
`avg_satisfaction`, `noshow_count`, `settled_at_tick`, `force_settled`,
`review_count`.
Phase Peer-A appends teach-spot identity fields (skill_topic,
host_skill_level, fee_breakdown, required_equipment, venue_type,
is_followup_session, bonded_partner_ids, teach_mode) and a
`fee_per_partner` derived property. SpotStatus enum is NOT extended —
Phase 1~3 states stay canonical.

Append only — Phase 1/2/3 fields must never be removed or renamed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from models.skills import FeeBreakdown


def _default_fee_breakdown() -> FeeBreakdown:
    """dataclass `default_factory` 용 `FeeBreakdown()` 헬퍼.

    Peer-A 이전에 생성된 legacy spot 은 `fee_breakdown` 을 넘기지 않으므로
    `FeeBreakdown()` (모든 항목 0) 이 default 로 들어간다. 이 경우
    `Spot.fee_per_partner == 0` 이 되어 legacy 코드가 fee 를 읽어도
    0 원 스팟으로 보인다.
    """

    return FeeBreakdown()


class SpotStatus(StrEnum):
    """Lifecycle status for a Spot."""

    # --- Phase 1 ---------------------------------------------------------
    OPEN = "OPEN"
    MATCHED = "MATCHED"
    CANCELED = "CANCELED"
    # --- Phase 2 additions (append only) ---------------------------------
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DISPUTED = "DISPUTED"
    # --- Phase 3 additions (append only) ---------------------------------
    # SETTLED       : COMPLETED → SETTLED via process_settlement (plan §4.3),
    #                 or DISPUTED → SETTLED via resolve_disputes 6h rule
    #                 (plan §4.5) when avg_satisfaction >= 0.5.
    # FORCE_SETTLED : DISPUTED → FORCE_SETTLED via resolve_disputes 24h rule
    #                 (plan §4.5) — host trust penalized.
    SETTLED = "SETTLED"
    FORCE_SETTLED = "FORCE_SETTLED"


@dataclass
class Spot:
    """A hosted meetup slot that agents can join.

    Phase 2 (plan §3.3 lifecycle + §3.4 processor) appends:
      - duration             : ticks the spot stays IN_PROGRESS before the
                               completion check runs (plan §3.4 example uses
                               spot.duration; 1~3 realistic, default 2).
      - confirmed_at_tick    : tick when status transitioned MATCHED→CONFIRMED
      - started_at_tick      : tick when status transitioned CONFIRMED→IN_PROGRESS
      - completed_at_tick    : tick when status transitioned IN_PROGRESS→COMPLETED
      - disputed_at_tick     : tick when status transitioned IN_PROGRESS→DISPUTED
                               (Phase 3 dispute timeout uses this, see plan §4.5)
      - canceled_at_tick     : tick of CANCEL (timeout OR all-participants-canceled)
      - checked_in           : set of agent_ids that CHECK_IN'd to this spot
      - noshow               : set of agent_ids that were marked NO_SHOW

    Phase 3 (plan §4.3 / §4.4) appends:
      - avg_satisfaction     : populated by `process_settlement`. `None` until
                               settlement runs so engine code can use it as a
                               "not yet settled" sentinel.
      - noshow_count         : convenience mirror of `len(spot.noshow)`, set
                               at settlement time (plan §4.4 reads
                               `spot.noshow_count` directly).
      - settled_at_tick      : tick at which SETTLED/FORCE_SETTLED happened.
      - force_settled        : True iff settlement went through the 24h
                               dispute-timeout path (plan §4.5).
      - review_count         : counter for reviews written for this spot
                               (incremented per WRITE_REVIEW emit).
    """

    spot_id: str
    host_agent_id: str
    region_id: str
    category: str
    capacity: int
    min_participants: int
    scheduled_tick: int  # tick at which the meetup is scheduled to start
    created_at_tick: int

    status: SpotStatus = SpotStatus.OPEN
    participants: list[str] = field(default_factory=list)

    # --- Phase 2 lifecycle additions (append only) -----------------------
    # (Phase 2) how many ticks the spot runs once IN_PROGRESS. Plan §3.4
    # uses `tick >= spot.scheduled_tick + spot.duration` as the completion
    # trigger. Realistic range is 1~3; default 2 so Phase 1 constructors
    # that omit this argument still work.
    duration: int = 2
    # (Phase 2) lifecycle timestamps. `None` until the transition happens.
    confirmed_at_tick: int | None = None
    started_at_tick: int | None = None
    completed_at_tick: int | None = None
    disputed_at_tick: int | None = None
    canceled_at_tick: int | None = None
    # (Phase 2) participant-level check-in / no-show tracking. `set` for
    # O(1) membership tests from `AgentState.checked_in_for`.
    checked_in: set[str] = field(default_factory=set)
    noshow: set[str] = field(default_factory=set)

    # --- Phase 3 settlement additions (append only) ----------------------
    # (Phase 3) average satisfaction across CHECKED_IN participants. `None`
    # until `process_settlement` runs (plan §4.3). Used by `resolve_disputes`
    # 6h rule (plan §4.5) to decide DISPUTED → SETTLED.
    avg_satisfaction: float | None = None
    # (Phase 3) cached `len(spot.noshow)` snapshot taken at settlement time.
    # Plan §4.4 calculate_satisfaction reads `spot.noshow_count` directly,
    # so the engine must set this before invoking the satisfaction function.
    noshow_count: int = 0
    # (Phase 3) tick at which status transitioned COMPLETED→SETTLED or
    # DISPUTED→{SETTLED,FORCE_SETTLED}. `None` until settlement runs.
    settled_at_tick: int | None = None
    # (Phase 3) True iff settlement went through `resolve_disputes` 24h
    # timeout path (plan §4.5). False for normal SETTLED.
    force_settled: bool = False
    # (Phase 3) number of WRITE_REVIEW events emitted for this spot.
    # Incremented by the settlement processor per generated review.
    review_count: int = 0

    # --- Phase Peer-A: teach-spot identity (append only) -----------------
    # peer-pivot §2-4 — "기존 spot" 을 "또래 강사 teach-spot" 으로 확장.
    # 모든 필드에 default 가 있어 legacy Phase 1~3 Spot 생성자가 기존
    # 인자만으로 그대로 동작한다.
    #
    # - skill_topic         : SkillTopic value (한국어). 빈 문자열("") 은
    #                         legacy spot 표지 — 엔진은 non-empty 일 때만
    #                         peer 결정 경로를 탄다.
    # - host_skill_level    : host 의 해당 스킬 level (0~5).
    # - fee_breakdown       : 2 층 fee (peer_labor + passthrough).
    #                         `Spot.fee_per_partner` property 로 파생.
    # - required_equipment  : partner 가 지참해야 하는 장비 목록. 부재 시
    #                         `suggest_fee_breakdown` 이 equipment_rental
    #                         을 반영.
    # - venue_type          : "none" | "cafe" | "home" | "studio" | "park"
    #                         | "gym" | "online". peer-pivot §3-4 fee 식에
    #                         분기 키로 쓰인다.
    # - is_followup_session : 단골 partner 대상 후속 세션 플래그.
    # - bonded_partner_ids  : 이 세션이 mentor_bond / friend 관계의 N 회차
    #                         인 partner 목록 (관계 전이 검사용).
    # - teach_mode          : "1:1" | "small_group" | "workshop" — fee
    #                         base_labor lookup 키.
    skill_topic: str = ""
    host_skill_level: int = 0
    fee_breakdown: FeeBreakdown = field(default_factory=_default_fee_breakdown)
    required_equipment: list[str] = field(default_factory=list)
    venue_type: str = "cafe"
    is_followup_session: bool = False
    bonded_partner_ids: list[str] = field(default_factory=list)
    teach_mode: str = "small_group"

    # --- Phase Peer-A+: counter-offer renegotiation (append only) --------
    # peer-pivot §3-counter — 모집 인원 미달 시 호스트가 파트너들에게
    # "N명이서 fee 재조정하고 진행할까요?" 역제안. 모든 필드 default 로
    # legacy Phase 1~3 / Phase Peer-A 코드 경로는 이 기능을 모르고 지나간다.
    #
    # - target_partner_count       : 원래 목표 모집 인원 (capacity 대신 목표).
    # - min_viable_count           : 이 미만이면 역제안도 불가 → cancel.
    # - wait_deadline_tick         : 이 tick 까지 target 미달이면 역제안 발동
    #                                조건 체크.
    # - counter_offer_sent         : 이미 한 번 보낸 경우 재발동 금지.
    # - counter_offer_sent_tick    : 언제 보냈는지 (응답 deadline 계산용).
    # - original_fee_breakdown     : 조정 전 원본 fee (리뷰/로그 용).
    # - renegotiation_history      : 각 재협상 회차의 스냅샷
    #     [{tick, from_count, to_count, from_total, to_total,
    #       accepted_by:[agent_ids], rejected_by:[agent_ids]}]
    target_partner_count: int = 0
    min_viable_count: int = 2
    wait_deadline_tick: int = -1
    counter_offer_sent: bool = False
    counter_offer_sent_tick: int = -1
    original_fee_breakdown: FeeBreakdown | None = None
    renegotiation_history: list[dict] = field(default_factory=list)

    # --- Phase Peer-A+: origination (offer vs request_matched) -----------
    # peer-pivot §3-request — 스팟이 어디서 시작됐는가. 호스트가 먼저 모집글
    # 올림(offer) vs 학생이 먼저 "배우고 싶어요" 요청을 올리고 호스트가 그에
    # 응답(request_matched). 매칭 이후 lifecycle 은 두 경로가 동일하지만,
    # content pipeline 은 이 필드로 리뷰/메시지의 voice (능동 주체) 를
    # 구분해 톤을 달리 생성한다.
    #
    # - origination_mode          : "offer" | "request_matched"
    # - origination_agent_id      : 첫 신호를 보낸 agent. offer 면 host 와 동일,
    #                               request_matched 면 learner(파트너).
    # - originating_request_id    : request_matched 일 때 원 SkillRequest 의 id.
    # - responded_at_tick         : request_matched 경로에서 호스트가 응답한 tick.
    origination_mode: str = "offer"
    origination_agent_id: str = ""
    originating_request_id: str | None = None
    responded_at_tick: int = -1

    # --- Phase Peer-A: derived properties --------------------------------
    @property
    def fee_per_partner(self) -> int:
        """Partner 1 인당 요금 = `fee_breakdown.total // capacity`.

        peer-pivot §2-4 주석: "기존 `fee_per_partner` 는
        `breakdown.total // partner_count` 로 파생한다". capacity 가 0 이거나
        fee_breakdown 이 default(all-zero) 이면 0 을 반환해 legacy fee
        읽기 경로와 충돌하지 않는다.
        """

        # Phase Peer-F bug fix (2025-04-15): fee_breakdown 의 모든 항목
        #   (peer_labor_fee, material_cost, venue_rental, equipment_rental)
        # 이 이미 per-partner 단위로 계산돼 있다 (engine/fee.py 주석
        # "(원/인)" 참조). 과거 구현은 total 을 capacity 로 다시 나눠서
        # fee_per_partner 가 실제 값의 1/capacity 로 축소되는 버그가 있었다
        # (peer sim 결과 fee=1,890원 같은 비현실적 저가의 근본 원인).
        if self.capacity <= 0:
            return 0
        return self.fee_breakdown.total
