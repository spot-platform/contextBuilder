"""price_breakdown deterministic draft.

POI-anchored pipeline §Phase 3c.

``FeeBreakdownSpec`` (peer_labor + material + venue + equipment) → ContentSpec
``PriceBreakdown`` (base_fee + included_items + optional_addons + refund_policy)
변환. LLM 호출 0회.

핵심 룰
-------
- ``base_fee`` = ``budget.expected_cost_per_person`` (1인 참가비, 이미 결정).
- ``included_items`` 은 fee_breakdown 의 비-zero 항목들을 자연어로 펼침.
- ``optional_addons`` 은 ``equipment_rental`` 이 있는 skill 만 추가 옵션화.
  ``mechanism`` 은 capacity / equipment_count 룰로 결정:
    - equipment_rental_per_partner 가 있고 capacity ≥ 4 면 ``funding`` (다 같이 N분의 1)
    - 그렇지 않으면 ``fixed`` (정액 추가)
    - venue_rental, material_cost 는 항상 ``realcost`` (실비)
- ``refund_policy`` 는 기본 72시간 cutoff.
"""
from __future__ import annotations

from typing import List, Optional

from pipeline.spec.models import (
    AddOn,
    FeeBreakdownSpec,
    IncludedItem,
    PriceBreakdown,
    RefundPolicy,
)


def build_price_breakdown_draft(
    *,
    base_fee: int,
    fee_breakdown: Optional[FeeBreakdownSpec],
    skill_topic: Optional[str],
    expected_count: int,
) -> PriceBreakdown:
    """fee_breakdown 의 4 항목을 ContentSpec PriceBreakdown 으로 변환."""

    included: List[IncludedItem] = []
    addons: List[AddOn] = []

    if fee_breakdown is not None:
        if fee_breakdown.material_cost > 0:
            per_person = fee_breakdown.material_cost
            included.append(
                IncludedItem(
                    name=_material_label(skill_topic),
                    value=f"1인 약 {per_person:,}원 상당",
                )
            )
        if fee_breakdown.venue_rental > 0:
            per_person = fee_breakdown.venue_rental
            included.append(
                IncludedItem(
                    name="공간 사용료",
                    value=f"1인 약 {per_person:,}원 분담",
                )
            )
        if fee_breakdown.peer_labor_fee > 0:
            included.append(
                IncludedItem(
                    name="가이드/진행",
                    value=f"호스트의 시간과 진행을 포함",
                )
            )
        if fee_breakdown.equipment_rental > 0:
            equip_per = fee_breakdown.equipment_rental
            mechanism = "funding" if expected_count >= 4 else "fixed"
            addons.append(
                AddOn(
                    name=_equipment_label(skill_topic),
                    price=int(equip_per),
                    mechanism=mechanism,  # type: ignore[arg-type]
                )
            )

    if not included:
        # 최소한 한 항목은 있어야 host onboarding 가이드 가치가 생긴다.
        included.append(
            IncludedItem(
                name="가이드/진행",
                value="호스트의 시간과 진행을 포함",
            )
        )

    refund_policy = RefundPolicy(
        cutoff_hours=72,
        full_refund_until="활동 3일 전까지",
        note="3~1일 전 50% / 당일 환불 불가",
    )

    return PriceBreakdown(
        base_fee=int(base_fee),
        included_items=included,
        optional_addons=addons,
        refund_policy=refund_policy,
    )


# ---------------------------------------------------------------------------
def _material_label(skill_topic: Optional[str]) -> str:
    if not skill_topic:
        return "재료비"
    table = {
        "홈쿡": "식재료",
        "홈베이킹": "베이킹 재료",
        "핸드드립": "원두 + 필터",
        "다도": "찻잎 + 다과",
        "김치 담그기": "배추 + 양념",
        "홈카페 라떼아트": "우유 + 원두",
        "원예": "씨앗 + 흙",
        "드로잉": "종이 + 연필",
        "수채화": "물감 + 종이",
        "캘리그라피": "붓펜 + 한지",
        "도예 기초": "점토 + 유약",
        "뜨개질": "실 + 바늘 소모분",
    }
    return table.get(skill_topic, "재료비")


def _equipment_label(skill_topic: Optional[str]) -> str:
    if not skill_topic:
        return "장비 대여"
    table = {
        "기타": "기타 대여",
        "우쿨렐레": "우쿨렐레 대여",
        "요가 입문": "요가매트 대여",
        "필라테스 스트레칭": "매트 대여",
        "볼더링": "클라이밍 슈즈 대여",
        "배드민턴": "라켓 + 셔틀콕",
        "탁구": "라켓 + 공",
    }
    return table.get(skill_topic, "장비 대여")


__all__ = ["build_price_breakdown_draft"]
