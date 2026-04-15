"""EventLog dataclass + factory + JSONL serializer — plan §2.3 Phase 1
and plan §3 Phase 2 event-type catalog and plan §4 Phase 3 catalog.

Phase 2 appends event_type values (no structural change to EventLog):
  CANCEL_JOIN, CHECK_IN, NO_SHOW, COMPLETE_SPOT        (agent actions)
  SPOT_TIMEOUT, SPOT_CONFIRMED, SPOT_STARTED,
  SPOT_COMPLETED, SPOT_DISPUTED                         (lifecycle emits)

Phase 3 appends event_type values (still no structural change):
  WRITE_REVIEW, SETTLE, VIEW_FEED, SAVE_SPOT           (agent actions)
  SPOT_SETTLED, FORCE_SETTLED, DISPUTE_RESOLVED         (lifecycle emits)

event_id is generated from a module-level monotonic counter (UUIDs are
too expensive for 2,400–1,680,000 tick loops per plan §1). The counter is
seed-resettable via `reset_event_counter()` so tests can re-run the same
simulation and get byte-identical event_id sequences.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Phase 2 event-type catalog
# ---------------------------------------------------------------------------
#
# Importable by tests and analysis.run_validate so the "did this run actually
# emit Phase 2 events?" gate has a single source of truth. EventLog.event_type
# remains a free-form `str` — this set is descriptive, not enforced.

PHASE2_EVENT_TYPES: set[str] = {
    # Agent actions added in Phase 2 (plan §3.2)
    "CANCEL_JOIN",
    "CHECK_IN",
    "NO_SHOW",
    "COMPLETE_SPOT",
    # Lifecycle processor emits (plan §3.4)
    "SPOT_TIMEOUT",
    "SPOT_CONFIRMED",
    "SPOT_STARTED",
    "SPOT_COMPLETED",
    "SPOT_DISPUTED",
}

# ---------------------------------------------------------------------------
# Phase 3 event-type catalog (plan §4)
# ---------------------------------------------------------------------------
#
# Importable by tests and analysis.run_validate so the "did this Phase 3 run
# emit settlement / review / feed / save events?" gate has a single source of
# truth. EventLog.event_type remains a free-form `str` — this set is
# descriptive, not enforced.
#
# Mapping to plan §4:
#   - WRITE_REVIEW     : agent action emitted by process_settlement when
#                        `random() < p_review` (plan §4.3 step 2).
#   - SETTLE           : agent action wrapper (plan §4.2 lists SETTLE among
#                        the 11 actions; engine may emit one per host on
#                        spot completion to bookkeep the settlement call).
#   - SPOT_SETTLED     : lifecycle emit when COMPLETED → SETTLED, or
#                        DISPUTED → SETTLED via the 6h dispute rule.
#   - FORCE_SETTLED    : lifecycle emit when DISPUTED → FORCE_SETTLED via
#                        the 24h dispute timeout (plan §4.5). Carries
#                        `payload={"reason": "dispute_timeout"}`.
#   - DISPUTE_RESOLVED : lifecycle emit by `resolve_disputes` 6h rule when a
#                        DISPUTED spot is upgraded to SETTLED.
#   - VIEW_FEED        : agent action — viewing the spot feed (plan §4.2);
#                        no spot_id, agent_id only.
#   - SAVE_SPOT        : agent action — bookmarking a spot into
#                        AgentState.saved_spots (plan §4.2).

PHASE3_EVENT_TYPES: set[str] = {
    "WRITE_REVIEW",
    "SETTLE",
    "SPOT_SETTLED",
    "FORCE_SETTLED",
    "DISPUTE_RESOLVED",
    "VIEW_FEED",
    "SAVE_SPOT",
}

# ---------------------------------------------------------------------------
# Phase Peer event-type catalog (peer-pivot §2-6)
# ---------------------------------------------------------------------------
#
# Append-only on top of PHASE2/PHASE3 catalogs. EventLog.event_type is still
# a free-form `str` — this set is descriptive (used by analysis / tests to
# gate "did this peer run emit skill/relationship/fee events?") and is NOT
# enforced by make_event.
#
# Mapping to peer-pivot §2-6:
#   - SKILL_SIGNAL         : agent 가 수요/공급 신호 표명. payload
#                            `{skill, role:"offer|request", motivation:0~1}`.
#   - CREATE_TEACH_SPOT    : teach-spot 생성 (CREATE_SPOT 의 peer 확장).
#                            payload `{skill, fee, teach_mode, venue_type}`.
#   - JOIN_TEACH_SPOT      : partner join (is_follower flag 포함). payload
#                            `{skill, is_follower:bool}`.
#   - SKILL_TRANSFER       : 수업 중 스킬 전수 (learner level 미세 증가).
#                            payload `{skill, level_gain:0.0~0.3}`.
#   - BOND_UPDATED         : first_meet → regular → mentor_bond 전이.
#                            payload `{from, to, sessions}`.
#   - FRIEND_UPGRADE       : mentor_bond → friend 전이. payload
#                            `{skill, sessions, avg_sat}`.
#   - REFERRAL_SENT        : 다른 agent 에게 추천 발화. payload
#                            `{host, skill, reason}`.
#   - EQUIPMENT_LENT       : 장비 대여. payload `{equipment, duration_ticks}`.
#   - POCKET_MONEY_EARNED  : 호스트 수익 발생. payload
#                            `{amount, spot_id, partner_count}`.
#   - REPUTATION_UPDATED   : 평판 EMA 업데이트. payload `{delta, new_score}`.

PHASE_PEER_EVENT_TYPES: set[str] = {
    "SKILL_SIGNAL",
    "CREATE_TEACH_SPOT",
    "JOIN_TEACH_SPOT",
    "SKILL_TRANSFER",
    "BOND_UPDATED",
    "FRIEND_UPGRADE",
    "REFERRAL_SENT",
    "EQUIPMENT_LENT",
    "POCKET_MONEY_EARNED",
    "REPUTATION_UPDATED",
    # ── Phase Peer-A+: Counter-offer renegotiation (plan §3-counter) ────
    # 모집 미달 시 호스트가 파트너들에게 "N명이서 fee 재조정하고 진행할까요?"
    # 역제안. 동의 여부에 따라 SPOT_RENEGOTIATED → CONFIRMED, 또는
    # rejections 누적 후 CANCELED.
    #
    #   - COUNTER_OFFER_SENT      : host → partners. payload
    #     {from_count, to_count, original_total, new_total}
    #   - COUNTER_OFFER_ACCEPTED  : partner → host. payload
    #     {partner_id, new_fee}
    #   - COUNTER_OFFER_REJECTED  : partner → host. payload
    #     {partner_id, reason:"budget"|"timing"|"other"}
    #   - SPOT_RENEGOTIATED       : lifecycle emit, 재협상 확정.
    #     payload {renegotiation_count, final_total, final_partner_count}
    "COUNTER_OFFER_SENT",
    "COUNTER_OFFER_ACCEPTED",
    "COUNTER_OFFER_REJECTED",
    "SPOT_RENEGOTIATED",
    # ── Phase Peer-A+: Offer vs Request dual path (plan §3-request) ─────
    # 학생이 먼저 "OO 배우고 싶어요" SkillRequest 게시 → 호스트 응답 →
    # Spot 생성 (origination_mode="request_matched") 경로. Spot lifecycle
    # 은 offer 경로와 동일하지만 voice(능동 주체) 가 학생.
    #
    #   - CREATE_SKILL_REQUEST    : learner 가 요청 게시. payload
    #     {request_id, skill, max_fee, mode, venue, deadline_tick}
    #   - SUPPORTER_RESPONDED     : host 가 open request 에 응답 →
    #     Spot 생성 트리거. payload
    #     {request_id, host_agent_id, proposed_fee, spot_id}
    #   - REQUEST_EXPIRED         : 마감 전까지 응답 없음.
    #     payload {request_id, reason:"no_response"|"learner_canceled"}
    "CREATE_SKILL_REQUEST",
    "SUPPORTER_RESPONDED",
    "REQUEST_EXPIRED",
}

# ---------------------------------------------------------------------------
# Monotonic event id counter (seed-resettable for deterministic tests)
# ---------------------------------------------------------------------------

_next_event_id: itertools.count = itertools.count(1)


def reset_event_counter(start: int = 1) -> None:
    """Reset the monotonic event_id counter.

    Call at the start of a simulation run (alongside `random.seed(...)`) to
    ensure event_id sequences are reproducible across runs with the same seed.
    """

    global _next_event_id
    _next_event_id = itertools.count(start)


def _alloc_event_id() -> int:
    return next(_next_event_id)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class EventLog:
    """A single row in the event log (one action or state transition).

    Phase 1 event_type values: CREATE_SPOT, JOIN_SPOT, NO_ACTION, SPOT_MATCHED.
    """

    event_id: int
    tick: int
    event_type: str
    agent_id: str | None
    spot_id: str | None
    region_id: str | None
    payload: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_event(
    tick: int,
    event_type: str,
    *,
    agent: Any | None = None,
    spot: Any | None = None,
    region_id: str | None = None,
    payload: dict | None = None,
) -> EventLog:
    """Build an `EventLog`, duck-typing ids off the passed objects.

    Accepts any object exposing `.agent_id`, `.spot_id`, `.region_id`
    attributes — avoids importing AgentState/Spot here so this module stays
    circular-import-safe. `None` is tolerated for any of the three entities.

    region_id resolution order:
      1. explicit `region_id=` kwarg (wins if given)
      2. spot.region_id
      3. agent.home_region_id (fallback when action is not spot-bound)
    """

    agent_id = getattr(agent, "agent_id", None) if agent is not None else None
    spot_id = getattr(spot, "spot_id", None) if spot is not None else None

    resolved_region: str | None
    if region_id is not None:
        resolved_region = region_id
    elif spot is not None:
        resolved_region = getattr(spot, "region_id", None)
    elif agent is not None:
        resolved_region = getattr(agent, "home_region_id", None)
    else:
        resolved_region = None

    return EventLog(
        event_id=_alloc_event_id(),
        tick=tick,
        event_type=event_type,
        agent_id=agent_id,
        spot_id=spot_id,
        region_id=resolved_region,
        payload=payload if payload is not None else {},
    )


# ---------------------------------------------------------------------------
# Serialization (JSONL — one event per line)
# ---------------------------------------------------------------------------


def serialize_event(e: EventLog) -> str:
    """Serialize an EventLog to a single-line JSON string.

    `sort_keys=True` is required so byte-identical seeds produce byte-identical
    log files (diff-friendly, reproducible test fixtures).
    """

    return json.dumps(asdict(e), ensure_ascii=False, sort_keys=True)
