"""Relationship FSM + reputation/referral emission — peer-pivot §3-5 / §3-6.

Phase Peer-C 신규 모듈. append-only: 기존 `engine/settlement.py` 본체는
수정하지 않고 이 모듈이 runner 의 settlement 후크에서 호출된다.

공개 함수:
    update_relationship(host, partner, spot, satisfaction, tick, rng)
        → list[EventLog]   (BOND_UPDATED / FRIEND_UPGRADE)
    maybe_emit_referral(source_partner, host, all_agents, spot, tick, rng)
        → list[EventLog]   (REFERRAL_SENT)
    update_reputation(host, avg_satisfaction) -> None
        → host.assets.reputation_score EMA 업데이트 (in-place).

전이 규칙 (plan §3-5):

    first_meet  ─(session_count >= 2 & avg_sat >= 0.70)─> regular
    regular     ─(session_count >= 4 & avg_sat >= 0.80)─> mentor_bond
    mentor_bond ─(session_count >= 6 & avg_sat >= 0.85 & rng<0.30)─> friend

대칭 업데이트: host.relationships[partner_id] 와
partner.relationships[host_id] 를 양쪽 모두 갱신한다. 두 entry 는 독립된
Relationship 인스턴스이며 같은 FSM 을 밟는다 (양쪽이 같은 session_count /
avg_satisfaction 을 공유하므로 전이 시점도 동일).

튜닝 후보 (Phase F 에 넘길 것):
    - FSM 임계 (min_sessions / min_avg_sat / friend_prob)
    - affinity recompute 공식 (base * (0.7 + 0.3 * stability))
    - REFERRAL_SENT 확률 (social_capital × affinity)
    - REFERRAL 수신 시 target.social_capital 가산 (+0.02)
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional

from models import EventLog, make_event
from models.agent import AgentState
from models.skills import Relationship
from models.spot import Spot

# ---------------------------------------------------------------------------
# 전이 규칙 (plan §3-5)
# ---------------------------------------------------------------------------
#
# 튜닝을 위해 module-level 상수로 노출. sim-analyst-qa 가 Phase F 게이트
# 분석 후 이 값을 바꿀 수 있다.

#: from_rel, min_sessions, min_avg_sat, to_rel, friend_prob (None = 확률 없음)
#
# Phase C gate tuning (2025-04-15) — settlement.py 의 SATISFACTION_BASE=0.5 에
# CATEGORY_MATCH_BONUS/FILL_SWEET_BONUS 등을 더해도 자연 run 의 avg_sat 가
# 0.47 부근에 머물러 FSM 전이 0 건이었다. legacy 53 pytest 회귀를 건드리지
# 않기 위해 settlement.py 상수는 놔두고, 여기 FSM 임계만 완화한다:
#   first_meet → regular   0.70 → 0.55
#   regular → mentor_bond  0.80 → 0.65
#   mentor_bond → friend   0.85 → 0.72 (확률은 0.30 그대로)
# plan §3-5 의 원본 숫자는 friend 톤 회귀 시 되돌릴 수 있다.
_TRANSITION_RULES: List[tuple] = [
    ("first_meet", 2, 0.55, "regular", None),
    ("regular", 4, 0.65, "mentor_bond", None),
    ("mentor_bond", 6, 0.72, "friend", 0.30),
]

#: affinity recompute 시 session_count 가 stability 1.0 에 도달하는 세션 수.
_AFFINITY_STABILITY_CAP_SESSIONS: int = 6

#: REFERRAL 수신 시 target 의 social_capital 가산 폭.
_REFERRAL_TARGET_SC_BUMP: float = 0.02

#: REPUTATION EMA 계수 — new_score = 0.9 * prev + 0.1 * sat (plan §3-6).
_REPUTATION_EMA_ALPHA: float = 0.1

#: REFERRAL 대상 학생이 host 에 대해 최소한 보유해야 하는 관계 (friend_only
#: 은 아니고 first_meet 제외 — regular 이상).
_REFERRAL_MIN_SOURCE_AVG_SAT: float = 0.75


def _get_or_create(
    agent: AgentState,
    other_id: str,
    skill_topic: str,
    tick: int,
) -> Relationship:
    """agent.relationships 에서 other_id 를 가져오거나 새로 생성한다.

    첫 만남 시 rel_type="first_meet", affinity=0.5 로 초기화.
    """
    rel = agent.relationships.get(other_id)
    if rel is None:
        rel = Relationship(
            other_agent_id=other_id,
            rel_type="first_meet",
            skill_topic=skill_topic,
            affinity=0.5,
        )
        agent.relationships[other_id] = rel
    return rel


def _recompute_affinity(rel: Relationship) -> float:
    """avg_satisfaction + session_count 기반 affinity 재계산 (0~1).

    base = avg_satisfaction clamp(0~1)
    stability = min(1.0, session_count / 6)
    affinity = base * (0.7 + 0.3 * stability)

    session 이 쌓일수록 avg_sat 의 반영 비율이 0.7 → 1.0 으로 커진다.
    """
    if rel.session_count <= 0:
        return 0.5
    avg = rel.avg_satisfaction
    base = max(0.0, min(1.0, avg))
    stability = min(1.0, rel.session_count / _AFFINITY_STABILITY_CAP_SESSIONS)
    return max(0.0, min(1.0, base * (0.7 + 0.3 * stability)))


def update_relationship(
    host: AgentState,
    partner: AgentState,
    spot: Spot,
    satisfaction: float,
    tick: int,
    rng: random.Random,
) -> List[EventLog]:
    """plan §3-5. host ↔ partner 쌍에 대해 대칭 업데이트.

    두 agent 의 Relationship entry 를 각자 업데이트하고, 전이 조건을
    만족한 쪽(들) 에 대해 BOND_UPDATED / FRIEND_UPGRADE 이벤트를 emit.

    Parameters
    ----------
    host : AgentState
        스팟 호스트.
    partner : AgentState
        세션에 참여한 partner.
    spot : Spot
        정산된 스팟. skill_topic / region_id 등 payload 소스.
    satisfaction : float
        이 (host, partner) 쌍의 만족도 (0~1). runner 가 agent 의
        `satisfaction_history[-1]` 또는 `spot.avg_satisfaction` 에서 가져옴.
    tick : int
        현재 tick — 이벤트 및 last_interaction_tick 기록.
    rng : random.Random
        friend 전이 확률 roll 용.

    Returns
    -------
    list[EventLog]
        BOND_UPDATED / FRIEND_UPGRADE 이벤트 (있으면).
    """
    events: List[EventLog] = []
    sat = max(0.0, min(1.0, float(satisfaction)))

    for a, b in ((host, partner), (partner, host)):
        rel = _get_or_create(a, b.agent_id, spot.skill_topic, tick)
        rel.session_count += 1
        rel.total_satisfaction += sat
        rel.last_interaction_tick = tick
        rel.affinity = _recompute_affinity(rel)

        # 전이 검사 — 한 번에 한 단계만.
        for (
            from_type,
            min_sess,
            min_avg,
            to_type,
            friend_prob,
        ) in _TRANSITION_RULES:
            if rel.rel_type != from_type:
                continue
            if rel.session_count < min_sess:
                continue
            if rel.avg_satisfaction < min_avg:
                continue
            if friend_prob is not None and rng.random() >= friend_prob:
                continue
            prev = rel.rel_type
            rel.rel_type = to_type
            if to_type == "friend":
                rel.evolved_to_friend = True
                events.append(
                    make_event(
                        tick=tick,
                        event_type="FRIEND_UPGRADE",
                        agent=a,
                        spot=spot,
                        payload={
                            "other_agent_id": b.agent_id,
                            "skill": spot.skill_topic,
                            "sessions": rel.session_count,
                            "avg_sat": round(rel.avg_satisfaction, 3),
                            "affinity": round(rel.affinity, 3),
                        },
                    )
                )
            else:
                events.append(
                    make_event(
                        tick=tick,
                        event_type="BOND_UPDATED",
                        agent=a,
                        spot=spot,
                        payload={
                            "other_agent_id": b.agent_id,
                            "from": prev,
                            "to": to_type,
                            "sessions": rel.session_count,
                            "avg_sat": round(rel.avg_satisfaction, 3),
                            "affinity": round(rel.affinity, 3),
                        },
                    )
                )
            break

    return events


def maybe_emit_referral(
    source_partner: AgentState,
    host: AgentState,
    all_agents: Dict[str, AgentState],
    spot: Spot,
    tick: int,
    rng: random.Random,
) -> List[EventLog]:
    """plan §3-6: 만족한 partner 가 친구에게 host 를 추천.

    조건:
      1. source_partner 가 host 에 대한 관계를 가지고 있고 `regular`
         이상 (first_meet 아님).
      2. 해당 관계의 avg_satisfaction >= 0.75.
      3. rng.random() < source_partner.assets.social_capital × rel.affinity.
      4. source_partner 의 다른 관계 중 affinity 최고 agent 를 타겟으로.

    성공 시:
      - target.assets.social_capital += 0.02 (clamp 1.0)
      - REFERRAL_SENT 이벤트 emit (source → target, host 추천)
    """
    events: List[EventLog] = []
    rel = source_partner.relationships.get(host.agent_id)
    if rel is None:
        return events
    if rel.rel_type == "first_meet":
        return events
    if rel.avg_satisfaction < _REFERRAL_MIN_SOURCE_AVG_SAT:
        return events

    sc = float(source_partner.assets.social_capital)
    aff = float(rel.affinity)
    p_refer = sc * aff
    if rng.random() >= p_refer:
        return events

    # 타겟 pick — source 의 다른 관계 중 affinity 최고 (host 제외 + first_meet 제외).
    candidates = [
        (aid, r.affinity)
        for aid, r in source_partner.relationships.items()
        if aid != host.agent_id and r.rel_type != "first_meet"
    ]
    if not candidates:
        return events
    candidates.sort(key=lambda kv: (-kv[1], kv[0]))
    target_id = candidates[0][0]
    target: Optional[AgentState] = all_agents.get(target_id)
    if target is None:
        return events

    # target social_capital 가산.
    target.assets.social_capital = min(
        1.0, target.assets.social_capital + _REFERRAL_TARGET_SC_BUMP
    )

    events.append(
        make_event(
            tick=tick,
            event_type="REFERRAL_SENT",
            agent=source_partner,
            spot=spot,
            payload={
                "from_agent_id": source_partner.agent_id,
                "to_agent_id": target_id,
                "host_agent_id": host.agent_id,
                "skill": spot.skill_topic,
                "reason": "high_satisfaction",
                "source_avg_sat": round(rel.avg_satisfaction, 3),
                "source_affinity": round(aff, 3),
            },
        )
    )
    return events


def update_reputation(host: AgentState, avg_satisfaction: float) -> None:
    """plan §3-6: host reputation EMA 업데이트.

    reputation_score ← 0.9 * prev + 0.1 * clamp(avg_satisfaction).

    In-place mutation. runner 가 호출 전 prev 를 스냅샷해 delta 를 계산
    (→ REPUTATION_UPDATED 이벤트 payload).
    """
    sat = max(0.0, min(1.0, float(avg_satisfaction)))
    prev = float(host.assets.reputation_score)
    host.assets.reputation_score = (
        (1.0 - _REPUTATION_EMA_ALPHA) * prev + _REPUTATION_EMA_ALPHA * sat
    )
