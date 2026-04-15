"""Counter-offer (역제안) 협상 루프 — plan §3-counter.

Phase Peer-B marketplace signature. 트리거 → 재계산 → partner 응답 수집
→ 최종 판정 4 단계가 이 파일에 전부 모여 있다. runner 의 `_run_peer`
tick 루프가 OPEN 스팟마다 `check_counter_offer_trigger` 를 호출하고,
트리거 통과 시 `send_counter_offer` 를 emit 한 뒤 3 tick 이 지나면
`finalize_counter_offer` 가 `SPOT_RENEGOTIATED` / `SPOT_TIMEOUT` 으로
lifecycle 을 마무리한다.
"""

from __future__ import annotations

import random
from typing import Dict, List

from models.agent import AgentState
from models.event import EventLog, make_event
from models.skills import FeeBreakdown
from models.spot import Spot, SpotStatus

# plan §3-counter: counter_offer_sent_tick + _RESPONSE_WAIT_TICKS 이 되면
# finalize. 기본 3 tick (config.peer.counter_offer_response_ticks 로 주입 가능).
DEFAULT_COUNTER_OFFER_RESPONSE_TICKS: int = 3

# plan §3-counter: labor_fee 할인율 (0.85 → 15% 감소).
_LABOR_DISCOUNT: float = 0.85

# partner 가 수락한 경우 affinity + _ACCEPT_AFFINITY_BUMP. plan §3-counter
# 마지막 문단 "수락한 partner 는 affinity +0.05".
_ACCEPT_AFFINITY_BUMP: float = 0.05


# ---------------------------------------------------------------------------
# 1. 트리거 체크
# ---------------------------------------------------------------------------


def check_counter_offer_trigger(spot: Spot, tick: int) -> bool:
    """plan §3-counter 트리거 조건 4 가지 체크.

    모두 True 여야 counter_offer 를 발동:
      * `spot.status == OPEN`
      * `counter_offer_sent == False` (이미 보냈으면 재발동 금지)
      * `tick >= wait_deadline_tick`  (wait_deadline_tick >= 0 인 경우만)
      * `min_viable_count <= len(participants) < target_partner_count`
    """

    if spot.status != SpotStatus.OPEN:
        return False
    if spot.counter_offer_sent:
        return False
    if spot.wait_deadline_tick < 0:
        # peer 경로가 아닌 legacy spot — 건너뛴다.
        return False
    if tick < spot.wait_deadline_tick:
        return False
    current = len(spot.participants)
    if current >= spot.target_partner_count:
        return False  # 이미 꽉 참 → 통상 매칭 경로로 진행
    if current < spot.min_viable_count:
        return False  # 너무 적어 역제안도 의미 없음 → 그냥 cancel 대기
    return True


# ---------------------------------------------------------------------------
# 2. 재계산 공식
# ---------------------------------------------------------------------------


def recompute_fee_for_smaller_group(
    original: FeeBreakdown,
    new_partner_count: int,
) -> FeeBreakdown:
    """plan §3-counter 재계산 공식.

    - passthrough 실비 (material / venue / equipment) 는 **총액 고정**.
      → partner 수가 줄면 1 인당 분담이 자동으로 증가 (`Spot.fee_per_partner`
      property 가 `total // capacity` 로 계산하므로 capacity 를 accepted
      명수로 재할당한다).
    - `peer_labor_fee` 는 15% 할인 (plan §3-counter "minor discount").

    Note: `new_partner_count` 는 현재 caller 에서는 log/snapshot 용도로만
    쓰이며, 실제 1 인당 분담 증가는 `spot.capacity = len(accepted)` 재할당
    시점에 `Spot.fee_per_partner` 가 자동 반영한다.
    """

    del new_partner_count  # log snapshot 용 placeholder

    return FeeBreakdown(
        peer_labor_fee=int(original.peer_labor_fee * _LABOR_DISCOUNT),
        material_cost=original.material_cost,
        venue_rental=original.venue_rental,
        equipment_rental=original.equipment_rental,
    )


# ---------------------------------------------------------------------------
# 3. 역제안 emit
# ---------------------------------------------------------------------------


def send_counter_offer(
    spot: Spot,
    host: AgentState,
    tick: int,
) -> List[EventLog]:
    """트리거 통과 시 호출. `original_fee_breakdown` 보존 → 재계산 적용 →
    `COUNTER_OFFER_SENT` 이벤트 1 개 반환.

    이 함수는 `counter_offer_sent` / `counter_offer_sent_tick` 을 세팅하지만
    `spot.status` 는 건드리지 않는다 (여전히 OPEN). 3 tick 뒤 finalize 가
    MATCHED 또는 CANCELED 로 확정한다.
    """

    # 원본 보존 — Phase D 콘텐츠 파이프라인이 "재조정 전/후 fee" 차이를
    # 리뷰/메시지에 써야 한다.
    spot.original_fee_breakdown = FeeBreakdown(
        peer_labor_fee=spot.fee_breakdown.peer_labor_fee,
        material_cost=spot.fee_breakdown.material_cost,
        venue_rental=spot.fee_breakdown.venue_rental,
        equipment_rental=spot.fee_breakdown.equipment_rental,
    )
    new_breakdown = recompute_fee_for_smaller_group(
        spot.fee_breakdown, len(spot.participants)
    )
    spot.fee_breakdown = new_breakdown
    spot.counter_offer_sent = True
    spot.counter_offer_sent_tick = tick

    ev = make_event(
        tick=tick,
        event_type="COUNTER_OFFER_SENT",
        agent=host,
        spot=spot,
        payload={
            "from_count": spot.target_partner_count,
            "to_count": len(spot.participants),
            "original_total": spot.original_fee_breakdown.total,
            "new_total": new_breakdown.total,
        },
    )
    return [ev]


# ---------------------------------------------------------------------------
# 4. 파트너 응답 확률
# ---------------------------------------------------------------------------


def p_accept_counter_offer(partner: AgentState, spot: Spot) -> float:
    """plan §3-counter 수락 확률 공식.

        affordability     = min(1, wallet / (new_fee * 3))
        relationship_boost = rel.affinity * 0.3   (단골이면 +)
        price_penalty     = max(0, ratio - 1) * 0.4
        p = affordability * 0.6 + relationship_boost - price_penalty

    `max(0.1, min(0.9, p))` 로 clip. `original_fee_breakdown` 이 없으면
    (Phase B 안전장치) 중립값 0.5 반환.
    """

    if spot.original_fee_breakdown is None:
        return 0.5

    # 재조정 후 1 인당 fee (accepted 수가 아직 확정되지 않았으므로
    # 현 participants 수 기준).
    current_count = max(1, len(spot.participants))
    new_fee = spot.fee_breakdown.total // current_count

    target = max(1, spot.target_partner_count)
    original_fee = spot.original_fee_breakdown.total // target

    if original_fee <= 0:
        return 0.5

    fee_delta_ratio = new_fee / original_fee if original_fee > 0 else 1.0

    assets = getattr(partner, "assets", None)
    wallet = int(getattr(assets, "wallet_monthly", 25_000)) if assets else 25_000
    denom = max(1, new_fee * 3)
    affordability = min(1.0, wallet / denom)

    relationship_boost = 0.0
    rels = getattr(partner, "relationships", None)
    if rels:
        rel = rels.get(spot.host_agent_id)
        if rel is not None:
            relationship_boost = float(rel.affinity) * 0.3

    price_penalty = max(0.0, fee_delta_ratio - 1.0) * 0.4

    p = affordability * 0.6 + relationship_boost - price_penalty
    if p < 0.1:
        return 0.1
    if p > 0.9:
        return 0.9
    return p


# ---------------------------------------------------------------------------
# 5. 응답 수집 → 재합성 → lifecycle 확정
# ---------------------------------------------------------------------------


def finalize_counter_offer(
    spot: Spot,
    host: AgentState,
    partners_lookup: Dict[str, AgentState],
    tick: int,
    rng: random.Random,
    *,
    response_wait_ticks: int = DEFAULT_COUNTER_OFFER_RESPONSE_TICKS,
) -> List[EventLog]:
    """counter_offer_sent_tick + `response_wait_ticks` 이후 응답 수집.

    수락 partner 가 `min_viable_count` 이상이면 MATCHED + SPOT_RENEGOTIATED
    emit. 미만이면 CANCELED + SPOT_TIMEOUT emit.

    호출자 (runner) 는 같은 tick 안에 `check_counter_offer_trigger →
    send_counter_offer` 와 `finalize_counter_offer` 를 모두 호출해도 된다
    (이 함수는 `tick < sent_tick + response_wait_ticks` 면 빈 리스트 반환).
    """

    events: List[EventLog] = []
    if not spot.counter_offer_sent:
        return events
    if tick < spot.counter_offer_sent_tick + response_wait_ticks:
        return events
    # 이미 finalize 됐으면 (status 가 OPEN 이 아님) 중복 방지.
    if spot.status != SpotStatus.OPEN:
        return events

    accepted: List[str] = []
    rejected: List[str] = []
    for pid in list(spot.participants):
        partner = partners_lookup.get(pid)
        if partner is None:
            # lookup 누락 → 안전하게 rejected 취급 (carry-over 피함).
            rejected.append(pid)
            continue
        p_acc = p_accept_counter_offer(partner, spot)
        if rng.random() < p_acc:
            accepted.append(pid)
            events.append(
                make_event(
                    tick=tick,
                    event_type="COUNTER_OFFER_ACCEPTED",
                    agent=partner,
                    spot=spot,
                    payload={
                        "partner_id": pid,
                        "new_fee": (
                            spot.fee_breakdown.total // max(1, len(spot.participants))
                        ),
                    },
                )
            )
        else:
            rejected.append(pid)
            events.append(
                make_event(
                    tick=tick,
                    event_type="COUNTER_OFFER_REJECTED",
                    agent=partner,
                    spot=spot,
                    payload={"partner_id": pid, "reason": "budget"},
                )
            )

    # renegotiation_history 스냅샷.
    original_total = (
        spot.original_fee_breakdown.total if spot.original_fee_breakdown else 0
    )
    spot.renegotiation_history.append(
        {
            "tick": tick,
            "from_count": spot.target_partner_count,
            "to_count": len(accepted),
            "from_total": original_total,
            "to_total": spot.fee_breakdown.total,
            "accepted_by": list(accepted),
            "rejected_by": list(rejected),
        }
    )

    if len(accepted) >= spot.min_viable_count:
        spot.participants = list(accepted)
        spot.capacity = len(accepted)
        spot.status = SpotStatus.MATCHED
        events.append(
            make_event(
                tick=tick,
                event_type="SPOT_RENEGOTIATED",
                spot=spot,
                payload={
                    "renegotiation_count": len(spot.renegotiation_history),
                    "final_total": spot.fee_breakdown.total,
                    "final_partner_count": len(accepted),
                },
            )
        )
        # plan §3-counter: 수락 partner 는 affinity +0.05.
        for pid in accepted:
            partner = partners_lookup.get(pid)
            if partner is None or not getattr(partner, "relationships", None):
                continue
            rel = partner.relationships.get(spot.host_agent_id)
            if rel is not None:
                rel.affinity = min(1.0, float(rel.affinity) + _ACCEPT_AFFINITY_BUMP)
    else:
        spot.status = SpotStatus.CANCELED
        spot.canceled_at_tick = tick
        events.append(
            make_event(
                tick=tick,
                event_type="SPOT_TIMEOUT",
                spot=spot,
                payload={"reason": "counter_offer_rejected"},
            )
        )

    return events
