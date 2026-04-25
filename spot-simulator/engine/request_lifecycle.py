"""SkillRequest → Spot 경로 (plan §3-request).

학생(learner)이 "OO 배우고 싶어요" 요청을 먼저 게시하고 호스트가 응답해
teach-spot 이 생성되는 경로. 매칭 이후 lifecycle 은 offer 경로와 동일하지만
`Spot.origination_mode == "request_matched"` 로 기록된다.

이 파일에는 4 개의 공개 함수가 있다:
  - `p_post_request`            : 학생이 SkillRequest 를 올릴 확률
  - `p_respond_to_request`      : 호스트가 open request 에 응답할 확률
  - `create_teach_spot_from_request` : 매칭 성립 시 Spot 생성
  - `process_open_requests`     : 매 tick 호출되는 top-level 처리기
"""

from __future__ import annotations

import random
from typing import Callable, Dict, List, Mapping, Sequence

from engine.fee import suggest_fee_breakdown
from engine.time_availability import time_availability
from engine._peer_math import level_floor_to_teach
from models.agent import AgentState
from models.event import EventLog, make_event
from models.skills import FeeBreakdown, SkillRequest  # noqa: F401
from models.spot import Spot, SpotStatus

MAX_FATIGUE: float = 1.0

# request_matched 경로의 기본 모집 인원. plan §3-request 예시에서 3 으로 고정.
_DEFAULT_REQUEST_SPOT_PARTNER_COUNT: int = 3
# request 매칭 시 scheduled_tick = tick + _DEFAULT_SCHEDULED_LEAD.
_DEFAULT_SCHEDULED_LEAD: int = 8
# request_matched spot 의 wait_deadline lead (tick).
_DEFAULT_WAIT_DEADLINE_LEAD: int = 12


# ---------------------------------------------------------------------------
# 1. 학생 요청 게시 확률
# ---------------------------------------------------------------------------


def p_post_request(
    learner: AgentState,
    skill: str,
    tick: int,
    *,
    catalog: Mapping[str, Mapping],
) -> float:
    """plan §3-request `p_post_request`.

    공식:
        learn_appetite × role_mod × (1 - fatigue/max) × time_availability
    role_mod:
        prefer_learn → 1.2, both → 1.0, prefer_teach → 0.2
    `learn_appetite < 0.3` 인 스킬은 0 반환 (약한 동기면 요청 게시 안 함).
    """

    del catalog  # 현재 미사용 — Phase C 에서 region density 등 추가 훅

    skills = getattr(learner, "skills", None) or {}
    sp = skills.get(skill)
    if sp is None or sp.learn_appetite < 0.3:
        return 0.0

    role_pref = getattr(learner, "role_preference", "both")
    role_mod = {"prefer_learn": 1.2, "both": 1.0, "prefer_teach": 0.2}.get(
        role_pref, 1.0
    )

    fatigue = float(getattr(learner, "fatigue", 0.0))
    fatigue_mod = max(0.0, min(1.0, 1.0 - fatigue / MAX_FATIGUE))

    p = sp.learn_appetite * role_mod * fatigue_mod * time_availability(learner, tick)
    if p < 0.0:
        return 0.0
    return p


# ---------------------------------------------------------------------------
# 2. 호스트 응답 확률
# ---------------------------------------------------------------------------


def p_respond_to_request(
    host: AgentState,
    request: SkillRequest,
    tick: int,
    *,
    catalog: Mapping[str, Mapping],
) -> float:
    """plan §3-request `p_respond_to_request`.

    - host 가 해당 skill 의 level_floor_to_teach 미만이면 0
    - suggest 된 fee 가 learner 예산 초과면 0
    - 기본 확률 = teach_appetite × pocket_money_motivation × relationship_boost
                  × fatigue_mod
    """

    del tick  # time 가중치는 호출자(process_open_requests)가 관리

    sp = None
    skills = getattr(host, "skills", None) or {}
    sp = skills.get(request.skill_topic)
    if sp is None:
        return 0.0
    if sp.level < level_floor_to_teach(request.skill_topic, catalog):
        return 0.0

    # 호스트가 제시하려는 fee 가 learner 예산 초과면 즉시 0.
    fb = suggest_fee_breakdown(
        host,
        request.skill_topic,
        request.preferred_teach_mode,
        request.preferred_venue,
        expected_partners=_DEFAULT_REQUEST_SPOT_PARTNER_COUNT,
        catalog=catalog,
    )
    proposed_per_partner = fb.total // _DEFAULT_REQUEST_SPOT_PARTNER_COUNT
    if proposed_per_partner > request.max_fee_per_partner:
        return 0.0

    relationship_boost = 1.0
    rels = getattr(host, "relationships", None) or {}
    rel = rels.get(request.learner_agent_id)
    if rel is not None:
        relationship_boost += float(rel.affinity) * 0.5

    assets = getattr(host, "assets", None)
    motivation = float(getattr(assets, "pocket_money_motivation", 0.5)) if assets else 0.5
    fatigue = float(getattr(host, "fatigue", 0.0))
    fatigue_mod = max(0.0, min(1.0, 1.0 - fatigue / MAX_FATIGUE))

    p = sp.teach_appetite * motivation * relationship_boost * fatigue_mod
    if p < 0.0:
        return 0.0
    return p


# ---------------------------------------------------------------------------
# 3. Spot 생성
# ---------------------------------------------------------------------------


def create_teach_spot_from_request(
    host: AgentState,
    request: SkillRequest,
    tick: int,
    *,
    catalog: Mapping[str, Mapping],
    spot_id_generator: Callable[[], str],
) -> Spot:
    """plan §3-request: 매칭 성립 시 Spot 을 생성. 호출자가 이 Spot 을
    spots 리스트에 append 해야 한다.

    - origination_mode="request_matched"
    - origination_agent_id = learner_agent_id (=request.learner_agent_id)
    - participants 에 learner 자동 포함
    """

    fb = suggest_fee_breakdown(
        host,
        request.skill_topic,
        request.preferred_teach_mode,
        request.preferred_venue,
        expected_partners=_DEFAULT_REQUEST_SPOT_PARTNER_COUNT,
        catalog=catalog,
    )

    spot_id = spot_id_generator()
    skills = getattr(host, "skills", None) or {}
    host_skill = skills.get(request.skill_topic)
    host_level = int(getattr(host_skill, "level", 0)) if host_skill else 0

    # legacy category 필드 — request 경로는 "teach" 로 고정. legacy path 의
    # category_match 어댑터는 peer 경로를 타지 않으므로 충돌 없음.
    scheduled = tick + _DEFAULT_SCHEDULED_LEAD
    spot = Spot(
        spot_id=spot_id,
        host_agent_id=host.agent_id,
        region_id=request.region_id,
        category="teach",
        capacity=_DEFAULT_REQUEST_SPOT_PARTNER_COUNT,
        min_participants=2,
        scheduled_tick=scheduled,
        created_at_tick=tick,
        skill_topic=request.skill_topic,
        host_skill_level=host_level,
        fee_breakdown=fb,
        venue_type=request.preferred_venue,
        teach_mode=request.preferred_teach_mode,
        target_partner_count=_DEFAULT_REQUEST_SPOT_PARTNER_COUNT,
        min_viable_count=2,
        wait_deadline_tick=tick + _DEFAULT_WAIT_DEADLINE_LEAD,
        origination_mode="request_matched",
        origination_agent_id=request.learner_agent_id,
        originating_request_id=request.request_id,
        responded_at_tick=tick,
        # FE handoff 2026-04-24: deterministic expected-close for FE
        # `spot.created` event, mirroring the offer path in runner.py.
        expected_closed_at_tick=scheduled + 2,
    )
    # learner 자동 참여 (본인이 올린 요청이니 당연히 join).
    spot.participants.append(request.learner_agent_id)
    return spot


# ---------------------------------------------------------------------------
# 4. tick 루프 — 매 tick 호출되는 top-level 처리기
# ---------------------------------------------------------------------------


def process_open_requests(
    agents: Sequence[AgentState],
    open_requests: List[SkillRequest],
    tick: int,
    rng: random.Random,
    *,
    catalog: Mapping[str, Mapping],
    spot_id_generator: Callable[[], str],
    new_spots_collector: List[Spot],
) -> List[EventLog]:
    """매 tick 호출되는 request_lifecycle top-level 처리기.

    각 OPEN request 에 대해:
      1. deadline 지났으면 EXPIRED 전이 + REQUEST_EXPIRED emit
      2. 호스트 후보 필터 (teach_appetite > 0, reputation_score > 0.3 등)
      3. 후보를 region 근접 순 정렬
      4. 각 후보에 대해 `rng.random() < p_respond_to_request` 면 Spot 생성
         → `SUPPORTER_RESPONDED` + `CREATE_TEACH_SPOT` 두 이벤트 emit
      5. 매칭 성립한 request 는 MATCHED 로 전이 (한 request 당 한 host)
    """

    events: List[EventLog] = []

    for request in list(open_requests):
        if request.status != "OPEN":
            continue

        if tick >= request.wait_deadline_tick:
            request.status = "EXPIRED"
            events.append(
                make_event(
                    tick=tick,
                    event_type="REQUEST_EXPIRED",
                    region_id=request.region_id,
                    payload={
                        "request_id": request.request_id,
                        "reason": "no_response",
                    },
                )
            )
            continue

        # 호스트 후보 필터.
        candidates: list[AgentState] = []
        for a in agents:
            if a.agent_id == request.learner_agent_id:
                continue
            skills = getattr(a, "skills", None) or {}
            sp = skills.get(request.skill_topic)
            if sp is None or sp.teach_appetite <= 0.0:
                continue
            assets = getattr(a, "assets", None)
            reputation = float(getattr(assets, "reputation_score", 0.5)) if assets else 0.5
            if reputation <= 0.3:
                continue
            candidates.append(a)

        # 결정성: region 근접 (0/1) → agent_id 2차 정렬.
        candidates.sort(
            key=lambda a: (
                0 if a.home_region_id == request.region_id else 1,
                a.agent_id,
            )
        )

        for host in candidates:
            p = p_respond_to_request(host, request, tick, catalog=catalog)
            if p <= 0.0:
                continue
            if rng.random() >= p:
                continue

            spot = create_teach_spot_from_request(
                host,
                request,
                tick,
                catalog=catalog,
                spot_id_generator=spot_id_generator,
            )
            new_spots_collector.append(spot)

            request.status = "MATCHED"
            request.matched_spot_id = spot.spot_id
            request.matched_at_tick = tick
            request.respondent_agent_id = host.agent_id

            # host.hosted_spots 에 추가해 legacy path 와 동일한 tracking 유지.
            try:
                host.hosted_spots.append(spot.spot_id)
            except AttributeError:
                pass

            events.append(
                make_event(
                    tick=tick,
                    event_type="SUPPORTER_RESPONDED",
                    agent=host,
                    spot=spot,
                    payload={
                        "request_id": request.request_id,
                        "host_agent_id": host.agent_id,
                        "proposed_fee": spot.fee_per_partner,
                        "spot_id": spot.spot_id,
                    },
                )
            )
            events.append(
                make_event(
                    tick=tick,
                    event_type="CREATE_TEACH_SPOT",
                    agent=host,
                    spot=spot,
                    payload={
                        "skill": spot.skill_topic,
                        "fee": spot.fee_per_partner,
                        "teach_mode": spot.teach_mode,
                        "venue_type": spot.venue_type,
                        "origination_mode": "request_matched",
                        # FE handoff 2026-04-24: align with offer-path payload
                        # so the BE publisher can emit `spot.created` from
                        # either origination path without branching.
                        "host_persona_id": host.agent_id,
                        "region_id": spot.region_id,
                        "scheduled_tick": spot.scheduled_tick,
                        "expected_closed_at_tick": spot.expected_closed_at_tick,
                        "capacity": spot.capacity,
                        "host_skill_level": spot.host_skill_level,
                        "fee_breakdown": {
                            "peer_labor_fee": spot.fee_breakdown.peer_labor_fee,
                            "material_cost": spot.fee_breakdown.material_cost,
                            "venue_rental": spot.fee_breakdown.venue_rental,
                            "equipment_rental": spot.fee_breakdown.equipment_rental,
                            "total": spot.fee_breakdown.total,
                            "passthrough_total": spot.fee_breakdown.passthrough_total,
                        },
                        # request-matched 는 FE 맥락에서 여전히 offer 이벤트로
                        # 보이지만, FE 가 voice 구분이 필요할 때 참고할 수 있도록
                        # intent 필드를 포함한다.
                        "intent": "request",
                    },
                )
            )
            break  # 한 request 에 한 host 만 매칭

    return events
