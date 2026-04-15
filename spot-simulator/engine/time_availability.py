"""Agent 시간 예산 대비 현재 tick 이 활동 가능한지 — plan §3-2.

`p_teach` / `p_learn` / `p_post_request` 셋 다 공통으로 쓰는 가벼운
time_availability 함수. weekday/weekend 예산 대비 "꽉 참 → 1.0, 비어 있음
→ 0.0" 선형 근사를 반환한다.

주: 실제 예산 소진(주간 누적 tick) 추적은 Phase C 에서 relationship 와
함께 붙일 예정. Phase B 에서는 정적 예산 기준 확률 weight 로만 사용한다.
"""

from __future__ import annotations

from models.agent import AgentState
from engine.time_utils import get_day_type

# 10 tick 누적 참여 가능을 "full" 기준으로 사용. plan §3-2 공식은 이 값을
# clamped weight (0~1) 로만 쓰므로 상수가 과하게 민감하지 않다.
_FULL_BUDGET = 10.0


def time_availability(agent: AgentState, tick: int) -> float:
    """주중/주말 예산 대비 잔여 여유도 (0~1).

    - weekday → `assets.time_budget_weekday`
    - weekend → `assets.time_budget_weekend`
    - 예산 0 → 0.0 (완전 불가)
    - 예산 >= 10 → 1.0 (full)

    Legacy agent (peer 필드 default) 의 경우 기본값 (weekday=3, weekend=10)
    이 들어가 있어 여전히 0.3 / 1.0 반환 → peer 결정 경로에서 적당히 낮은
    weight 로 동작한다.
    """

    # Assets 는 __post_init__ 으로 항상 존재하지만 테스트 mock 안전하게.
    assets = getattr(agent, "assets", None)
    if assets is None:
        return 0.3

    day_type = get_day_type(tick)
    if day_type == "weekday":
        budget = int(getattr(assets, "time_budget_weekday", 3))
    else:
        budget = int(getattr(assets, "time_budget_weekend", 10))

    if budget <= 0:
        return 0.0
    return min(1.0, budget / _FULL_BUDGET)
