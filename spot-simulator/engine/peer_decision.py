"""Peer 의사결정 공식 — plan §3-2 / §3-3.

`p_teach` / `p_learn` 는 agent 단위로 매 tick 호출되고,
`find_matchable_teach_spot` 은 learner 가 open teach-spot 후보를 훑을
때 사용한다. `pick_skill_to_teach` / `pick_teach_mode` 는 host 가 이번
tick 에 teach-spot 을 열기로 했을 때 보조 선택기.

legacy `engine/decision.py` 의 `p_create` / `p_join` / `find_matchable_spots`
/ `pick_best_spot` 은 **건드리지 않는다** — runner 의 simulation_mode 분기
로 peer / legacy 경로가 완전히 분리된다 (plan §7-3 append-only).
"""

from __future__ import annotations

import random
from typing import Mapping, Sequence

from engine.fee import budget_capability
from engine.time_availability import time_availability
from engine._peer_math import level_floor_to_teach
from models.agent import AgentState
from models.skills import SkillProfile  # noqa: F401 — type hint source
from models.spot import Spot, SpotStatus

# plan §3-2 상수. fatigue 는 [0, 1] 정규화 상태로 들어온다.
MAX_FATIGUE: float = 1.0


def _fatigue_mod(agent: AgentState) -> float:
    """plan §3-2 `1 - fatigue/max_fatigue` 항. clamp 후 반환."""

    f = float(getattr(agent, "fatigue", 0.0))
    if MAX_FATIGUE <= 0:
        return 1.0
    v = 1.0 - (f / MAX_FATIGUE)
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _get_skill_profile(agent: AgentState, skill: str) -> SkillProfile | None:
    skills = getattr(agent, "skills", None)
    if not skills:
        return None
    return skills.get(skill)


def p_teach(
    agent: AgentState,
    skill: str,
    tick: int,
    *,
    catalog: Mapping[str, Mapping],
) -> float:
    """plan §3-2 `p_teach`. host 가 이 skill 로 teach-spot 을 열 확률.

    공식:
        teach_appetite
        × (level / 5)
        × pocket_money_motivation
        × (1 - fatigue / max_fatigue)
        × time_availability(agent, tick)
        × space_mod
    """

    sp = _get_skill_profile(agent, skill)
    if sp is None or sp.teach_appetite <= 0.0:
        return 0.0

    floor = level_floor_to_teach(skill, catalog)
    if sp.level < floor:
        return 0.0

    assets = getattr(agent, "assets", None)
    motivation = float(getattr(assets, "pocket_money_motivation", 0.5)) if assets else 0.5
    space_level = int(getattr(assets, "space_level", 1)) if assets else 1

    # plan §3-2: "space 가산: home 보유 호스트는 +20%"
    space_mod = 1.2 if space_level >= 2 else 1.0

    p = (
        sp.teach_appetite
        * (sp.level / 5.0)
        * motivation
        * _fatigue_mod(agent)
        * time_availability(agent, tick)
        * space_mod
    )
    if p < 0.0:
        return 0.0
    return p


def p_learn(
    agent: AgentState,
    skill: str,
    tick: int,
    fee_per_partner: int,
) -> float:
    """plan §3-2 `p_learn`. learner 가 해당 fee 의 teach-spot 에 join 할 확률.

    공식:
        learn_appetite
        × (1 - level / 5)           # 이미 잘하면 배울 동기 ↓
        × budget_capability(wallet, fee)
        × (1 - fatigue / max_fatigue)
        × time_availability(agent, tick)
    """

    sp = _get_skill_profile(agent, skill)
    if sp is None or sp.learn_appetite <= 0.0:
        return 0.0

    level_headroom = 1.0 - (sp.level / 5.0)
    if level_headroom <= 0.0:
        return 0.0

    assets = getattr(agent, "assets", None)
    wallet = int(getattr(assets, "wallet_monthly", 25_000)) if assets else 25_000
    wallet_mod = budget_capability(wallet, fee_per_partner)

    p = (
        sp.learn_appetite
        * level_headroom
        * wallet_mod
        * _fatigue_mod(agent)
        * time_availability(agent, tick)
    )
    if p < 0.0:
        return 0.0
    return p


def p_join_bonded(partner: AgentState, host_id: str) -> float:
    """plan §3-2 `p_join_bonded`. 단골 관계면 기본 확률에 가산할 multiplier.

    - first_meet → 1.0 (가산 없음)
    - regular / mentor_bond / friend → `1 + min(1.0, rel.affinity)` ≤ 2.0
    """

    rels = getattr(partner, "relationships", None)
    if not rels:
        return 1.0
    rel = rels.get(host_id)
    if rel is None:
        return 1.0
    if rel.rel_type == "first_meet":
        return 1.0
    boost = min(1.0, float(rel.affinity))
    return 1.0 + boost


def find_matchable_teach_spot(
    learner: AgentState,
    spots: Sequence[Spot],
    tick: int,
    *,
    catalog: Mapping[str, Mapping],
) -> Spot | None:
    """plan §3-3 매칭 함수.

    우선순위 (plan §3-3 주석):
      1. bonded host 의 OPEN teach-spot (가산)
      2. learn_appetite 가 높은 skill
      3. region 근접 (home_region 동일하면 +40%)
      4. fee 가 wallet 대비 무리 없음 (p_learn 안에 포함)
      5. required_equipment 충족 — Phase B 에서는 단순화: host_skill_level 만 본다.
    """

    del catalog  # 현재 스코어에는 쓰이지 않음 — 남겨두면 Phase C 에서 쓸 훅

    candidates: list[tuple[float, Spot]] = []
    for s in spots:
        if s.status != SpotStatus.OPEN:
            continue
        if not s.skill_topic:
            # legacy spot (skill_topic=="") 은 peer 경로에서 건너뛴다.
            continue
        if s.host_agent_id == learner.agent_id:
            continue
        if learner.agent_id in s.participants:
            continue
        if s.capacity > 0 and len(s.participants) >= s.capacity:
            continue

        fee = s.fee_per_partner
        base_p = p_learn(learner, s.skill_topic, tick, fee)
        if base_p <= 0.0:
            continue

        bonded = p_join_bonded(learner, s.host_agent_id)
        region_mod = 1.0 if s.region_id == learner.home_region_id else 0.7
        score = base_p * bonded * region_mod
        if score <= 0.0:
            continue
        candidates.append((score, s))

    if not candidates:
        return None

    # Determinism: tie-break by spot_id so a given (learner, spots) pair
    # always picks the same spot, independent of list order.
    candidates.sort(key=lambda item: (-item[0], item[1].spot_id))
    return candidates[0][1]


def pick_skill_to_teach(
    host: AgentState,
    tick: int,
    *,
    catalog: Mapping[str, Mapping],
    rng: random.Random,
) -> str | None:
    """호스트가 이번 tick 에 teach-spot 을 열기로 했을 때 어떤 스킬을 고를지.

    teach_appetite × (level / 5) 가중합 샘플링. level_floor 미만이거나
    weight == 0 인 스킬은 후보에서 제외. 샘플 가능한 후보가 없으면 None.
    """

    del tick  # time weight 는 이미 p_teach 에서 필터됨

    skills = getattr(host, "skills", None) or {}
    weights: list[tuple[str, float]] = []
    for skill, sp in skills.items():
        if sp.teach_appetite <= 0.0:
            continue
        if sp.level < level_floor_to_teach(skill, catalog):
            continue
        w = sp.teach_appetite * (sp.level / 5.0)
        if w > 0.0:
            weights.append((skill, w))

    if not weights:
        return None

    # 결정성: 후보 정렬(가중치 내림차순, skill 이름 오름차순).
    weights.sort(key=lambda item: (-item[1], item[0]))

    total = sum(w for _, w in weights)
    r = rng.random() * total
    acc = 0.0
    for skill, w in weights:
        acc += w
        if r <= acc:
            return skill
    return weights[-1][0]


def pick_teach_mode(
    skill: str,
    catalog: Mapping[str, Mapping],
    rng: random.Random,
) -> str:
    """`skills_catalog.yaml` `teach_mode_distribution` 기반 샘플링.

    catalog 에 distribution 이 없으면 ``small_group`` 고정 반환.
    """

    spec = catalog.get(skill) if catalog else None
    dist: dict[str, float] | None = None
    if spec:
        dist = spec.get("teach_mode_distribution")  # type: ignore[assignment]
    if not dist:
        return "small_group"

    items = sorted(dist.items(), key=lambda kv: (-float(kv[1]), kv[0]))
    r = rng.random()
    acc = 0.0
    for mode, p in items:
        acc += float(p)
        if r <= acc:
            return mode
    return items[-1][0]


def pick_venue(
    skill: str,
    catalog: Mapping[str, Mapping],
    rng: random.Random,
) -> str:
    """스킬의 venue 를 샘플링.

    우선순위:
      1. catalog[skill]["venue_distribution"] (optional dict {venue: prob})
         → 정규화 후 샘플.
      2. 없으면 catalog[skill]["default_venue"].
      3. 그것도 없으면 ``"cafe"``.

    `CREATE_SKILL_REQUEST` 생성에서 venue 편향을 풀기 위한 훅. catalog 에
    ``venue_distribution`` 키를 넣지 않은 skill 은 기존과 동일하게 동작한다.
    """

    spec = catalog.get(skill) if catalog else None
    if not spec:
        return "cafe"

    dist = spec.get("venue_distribution")  # type: ignore[assignment]
    if isinstance(dist, dict) and dist:
        items = sorted(dist.items(), key=lambda kv: (-float(kv[1]), kv[0]))
        total = sum(float(v) for _, v in items) or 1.0
        r = rng.random() * total
        acc = 0.0
        for venue, p in items:
            acc += float(p)
            if r <= acc:
                return venue
        return items[-1][0]

    return spec.get("default_venue", "cafe")
