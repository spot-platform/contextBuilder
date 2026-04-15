"""Peer fee 계산 — plan §3-4.

`skills_catalog.yaml` 에서 material / venue / equipment rental 을 읽어 2 층
fee (peer_labor + passthrough) 를 합성한다. 3 단 상한 게이트 (LABOR_CAP /
SOFT_CAP / HARD_CAP) 는 `models.skills` 의 상수를 재사용한다.

이 모듈은 **legacy 경로에서는 절대 import 되지 않는다.** peer 경로의
`engine/peer_decision.py`, `engine/negotiation.py`, `engine/request_lifecycle.py`
만 호출한다.
"""

from __future__ import annotations

from typing import Mapping

from models.skills import (
    FeeBreakdown,
    LABOR_CAP_PER_PARTNER,
    SOFT_CAP_PER_PARTNER,
    HARD_CAP_PER_PARTNER,
)


# ---------------------------------------------------------------------------
# base_labor lookup — plan §3-4 공식
# ---------------------------------------------------------------------------
#
# teach_mode 별 host 의 "시간/노동" 기본 가격 (원/인). plan §3-4 표 그대로.
#   - 1:1          : 집중도 높음 → 7,000
#   - small_group  : 2~4명      → 4,000
#   - workshop     : 5명+        → 2,500

# Phase Peer-F 재튜닝 (2025-04-15): plan §3-4 의 7/4/2.5k 값으로 나온
# 실제 peer sim 결과가 평균 labor 3~7k, 일부는 1,800원대까지 내려가 "책임감
# 생길 수준" 미달이었다. 또래 강사라도 2만원짜리 수업에 labor 1만원 정도는
# 남아야 한다는 사용자 피드백 반영 → 상향 조정. LABOR_CAP 도 10k → 18k.
# legacy 53 pytest 회귀는 engine/decision.py (p_create/p_join) 를 건드리지
# 않으므로 영향 없다.
_BASE_LABOR: dict[str, int] = {
    "1:1": 12_000,
    "small_group": 7_000,
    "workshop": 4_500,
}


# ---------------------------------------------------------------------------
# venue_type 별 기본 대관료 (catalog 에서 파생되지 않는 값)
# ---------------------------------------------------------------------------
#
# studio / gym 은 catalog 의 `studio_rental_total` / `gym_rental_total` 을
# partner 수로 나눠 분배한다 (아래 suggest_fee_breakdown 내부 분기).
# 그 외 venue 는 이 테이블로 고정값 사용.

_VENUE_BASE_RENTAL: dict[str, int] = {
    "home": 0,
    "park": 0,
    "cafe": 2_000,  # 카페 음료 1잔 수준 (partners 로 나누지 않음)
    "online": 0,
    "none": 0,
}


def suggest_fee_breakdown(
    host,
    skill: str,
    teach_mode: str,
    venue_type: str,
    expected_partners: int,
    catalog: Mapping[str, Mapping],
) -> FeeBreakdown:
    """plan §3-4 공식 그대로. `host` 는 duck-typed AgentState 이다.

    요구 attribute:
      - host.skills[skill].level (없으면 0 취급)
      - host.assets.pocket_money_motivation
      - host.assets.equipment (set[str])

    `catalog` 는 `data.loader.load_skills_catalog()` 결과.
    """

    spec: Mapping = catalog.get(skill) or {}
    expected_partners = max(1, int(expected_partners))

    # ── 1. peer_labor_fee (plan §3-4 공식) ────────────────────────────
    base = _BASE_LABOR.get(teach_mode, _BASE_LABOR["small_group"])
    sp = None
    try:
        sp = host.skills.get(skill) if getattr(host, "skills", None) else None
    except AttributeError:
        sp = None
    level = int(getattr(sp, "level", 0)) if sp is not None else 0
    # Phase Peer-F 재튜닝: level 과 motivation 의 기여도를 살짝 상향.
    # level_mod: 0.6+L*0.15 → 0.75+L*0.12 (L3=1.11, L4=1.23, L5=1.35)
    # motivation_mod: 0.8+pmm*0.4 → 0.90+pmm*0.25 (pmm 0.85 ≈ 1.11)
    level_mod = 0.75 + level * 0.12

    motivation = 0.5
    try:
        motivation = float(host.assets.pocket_money_motivation)
    except AttributeError:
        motivation = 0.5
    motivation_mod = 0.90 + motivation * 0.25

    labor = int(base * level_mod * motivation_mod)
    labor = min(labor, LABOR_CAP_PER_PARTNER)
    labor = max(0, labor)

    # ── 2. material_cost (partner 1 인당 고정) ────────────────────────
    material = int(spec.get("material_cost_per_partner", 0) or 0)

    # ── 3. venue_rental ───────────────────────────────────────────────
    if venue_type == "studio":
        total = int(spec.get("studio_rental_total", 0) or 0)
        venue_rental = total // expected_partners
    elif venue_type == "gym":
        total = int(spec.get("gym_rental_total", 0) or 0)
        venue_rental = total // expected_partners
    else:
        venue_rental = _VENUE_BASE_RENTAL.get(venue_type, 0)

    # ── 4. equipment_rental (host 가 장비 없을 때) ─────────────────────
    equipment_rental = 0
    try:
        equipment_set = host.assets.equipment
    except AttributeError:
        equipment_set = set()
    if equipment_set is None:
        equipment_set = set()
    if skill not in equipment_set:
        equipment_rental = int(spec.get("equipment_rental_per_partner", 0) or 0)

    return FeeBreakdown(
        peer_labor_fee=labor,
        material_cost=material,
        venue_rental=venue_rental,
        equipment_rental=equipment_rental,
    )


def budget_capability(partner_wallet: int, fee_per_partner: int) -> float:
    """plan §3-2 `p_learn` 공식의 wallet 항.

    ``ratio = fee_per_partner / max(1, wallet) * 100`` 으로 % 를 내고,
    3% 이하면 1.0, 30% 이상이면 0.1, 그 사이는 선형 감쇠.
    """

    if fee_per_partner <= 0:
        return 1.0
    wallet = max(1, int(partner_wallet))
    ratio = fee_per_partner / wallet * 100.0
    if ratio <= 3.0:
        return 1.0
    if ratio >= 30.0:
        return 0.1
    return 1.0 - (ratio - 3.0) / 27.0 * 0.9


def validate_fee_caps(breakdown: FeeBreakdown) -> tuple[bool, str]:
    """3 단 상한 게이트. 엔진이 suggest 한 값이 domain 불변식을 유지하는지
    체크하는 디버그/assert 용. validator (Phase E) 가 쓰는 규칙과 동일.

    Returns `(ok, reason)`. `ok=False` 시 reason 은 아래 중 하나:
      - ``labor_cap_exceeded``   — peer_labor_fee > LABOR_CAP
      - ``hard_cap_exceeded``    — total > HARD_CAP
      - ``soft_cap_no_passthrough`` — total > SOFT_CAP && passthrough == 0
    """

    if breakdown.peer_labor_fee > LABOR_CAP_PER_PARTNER:
        return False, "labor_cap_exceeded"
    if breakdown.total > HARD_CAP_PER_PARTNER:
        return False, "hard_cap_exceeded"
    if breakdown.total > SOFT_CAP_PER_PARTNER and breakdown.passthrough_total == 0:
        return False, "soft_cap_no_passthrough"
    return True, ""
