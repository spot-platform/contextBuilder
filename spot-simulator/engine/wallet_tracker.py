"""Wallet / earnings soft-tracker — peer-pivot §3-6 / §8-3.

Phase Peer-C 신규 모듈. 기존 settlement 본체를 수정하지 않고, runner 가
JOIN / SETTLEMENT 훅에서 얇은 래퍼로 호출한다.

공개 함수:
    charge_partner_on_join(partner, spot) -> None
        partner 가 teach-spot 에 join 확정될 때 fee 소프트 차감.
        - partner.assets.spent_total += fee
        - partner.assets.wallet_monthly -= fee (0 floor)

    credit_host_on_settlement(host, spot, tick) -> list[EventLog]
        settlement 시 host 수익(= peer_labor_fee × partner_count) 을
        assets.earn_total / wallet_monthly 에 누적하고
        POCKET_MONEY_EARNED 이벤트 emit.

    record_reputation_update(host, tick, delta) -> list[EventLog]
        relationships.update_reputation() 호출 전후의 delta 를 받아
        REPUTATION_UPDATED 이벤트 emit (얇은 헬퍼).

MVP 범위 (Phase C):
    - 월 리셋(wallet_monthly) 은 아직 구현 안 함. plan §7-2 표에
      "월 리셋 (Phase B 선택 사항)" 으로 기재되어 있어 Phase F 튜닝 시
      결정. 현재는 wallet_monthly 가 누적 잔고 역할.
    - passthrough (material/venue/equipment rental) 은 host 수익이 아님
      (실비 pass-through) — earn_total 에 포함되지 않는다. 오직
      peer_labor_fee 만 순마진으로 카운트.

Phase F 튜닝 후보:
    - 월 리셋 주기 (현재: 없음. 제안: 336 tick = 1 week 또는 2 week)
    - passthrough 총액을 wallet 에 일시 예치 후 settlement 시 외부로
      빠져나가는 모델 (현재: partner 쪽에서만 차감)
    - host 장비/재료비 선지출 반영 (현재: 없음)
"""

from __future__ import annotations

from typing import List

from models import EventLog, make_event
from models.agent import AgentState
from models.spot import Spot


def charge_partner_on_join(partner: AgentState, spot: Spot) -> None:
    """partner 가 spot 에 join 확정 시 fee_per_partner 를 소프트 차감.

    `fee_per_partner` 가 0 이거나 음수이면 아무 것도 하지 않는다
    (legacy Phase 1~3 spot 이 peer 경로로 흘러들어올 때의 fallback).
    `wallet_monthly` 는 0 floor — 적자 지갑은 허용하지 않는다
    (실세계 지갑 모델과 유사).
    """
    fee = int(getattr(spot, "fee_per_partner", 0) or 0)
    if fee <= 0:
        return
    assets = partner.assets
    assets.spent_total += fee
    new_balance = assets.wallet_monthly - fee
    assets.wallet_monthly = max(0, new_balance)


def credit_host_on_settlement(
    host: AgentState,
    spot: Spot,
    tick: int,
) -> List[EventLog]:
    """settlement 시점: host 에게 peer_labor_fee × partner_count 를 누적.

    Parameters
    ----------
    host : AgentState
    spot : Spot
        반드시 `fee_breakdown.peer_labor_fee` 가 set 되어 있어야 함.
    tick : int

    Returns
    -------
    list[EventLog]
        POCKET_MONEY_EARNED 이벤트 1 건 (수익이 0 이면 빈 리스트).
    """
    events: List[EventLog] = []
    # partner 수 = participants 길이 (host 제외). legacy spot 은
    # participants 가 비어 있어 0 건 처리.
    partner_count = len(spot.participants)
    if partner_count <= 0:
        return events
    labor = int(getattr(spot.fee_breakdown, "peer_labor_fee", 0) or 0)
    if labor <= 0:
        return events

    revenue = labor * partner_count
    host.assets.earn_total += revenue
    # 지갑 환원 (소프트 모델).
    host.assets.wallet_monthly += revenue

    events.append(
        make_event(
            tick=tick,
            event_type="POCKET_MONEY_EARNED",
            agent=host,
            spot=spot,
            payload={
                "amount": revenue,
                "spot_id": spot.spot_id,
                "partner_count": partner_count,
                "labor_per_partner": labor,
                "new_wallet": host.assets.wallet_monthly,
                "earn_total": host.assets.earn_total,
            },
        )
    )
    return events


def record_reputation_update(
    host: AgentState,
    tick: int,
    delta: float,
    spot: Spot | None = None,
) -> List[EventLog]:
    """REPUTATION_UPDATED 이벤트 emit — delta != 0 인 경우에만.

    Parameters
    ----------
    host : AgentState
    tick : int
    delta : float
        `new_reputation - prev_reputation`. runner 가
        `relationships.update_reputation` 호출 전후로 계산.
    spot : Spot | None
        이벤트의 spot_id 를 채우기 위한 컨텍스트. None 이면 agent 기반.
    """
    # 매우 작은 노이즈는 스팸을 막기 위해 이벤트로 내지 않을 수도 있지만,
    # Phase F 분석을 위해 현재는 모든 update 를 기록한다.
    return [
        make_event(
            tick=tick,
            event_type="REPUTATION_UPDATED",
            agent=host,
            spot=spot,
            payload={
                "delta": round(float(delta), 4),
                "new_score": round(float(host.assets.reputation_score), 4),
            },
        )
    ]
