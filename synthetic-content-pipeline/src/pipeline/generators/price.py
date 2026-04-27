"""SpotPriceGenerator — Phase 4c (POI-anchored).

ContentSpec.price_breakdown (deterministic draft) 를 받아 자연어 표현만
LLM 이 다듬는 Generator. base_fee / mechanism 은 변경 금지.

schema: ``src/pipeline/llm/schemas/price_v1.json``
프롬프트: ``config/prompts/price/v1.j2``
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict, List

from pipeline.generators.base import BaseGenerator
from pipeline.spec.models import ContentSpec


class SpotPriceGenerator(BaseGenerator):
    """가격 분해 생성기."""

    content_type: str = "price"
    template_id: str = "price:v1"
    template_path: str = "price/v1.j2"
    schema_path: Path = (
        Path(__file__).resolve().parent.parent / "llm" / "schemas" / "price_v1.json"
    )

    def __init__(self) -> None:
        if not self.schema_path.exists():
            warnings.warn(
                f"{self.template_id}: schema not found at {self.schema_path}",
                stacklevel=2,
            )

    def _placeholder_payload(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """stub 폴백 — spec.price_breakdown 그대로 schema 형태로 펼침."""
        pb = variables.get("price_breakdown")
        cost = int(variables.get("budget_cost_per_person", 15000))
        if not pb:
            return {
                "base_fee": cost,
                "included_items": [
                    {"name": "가이드/진행", "value": "호스트의 시간과 진행을 포함해요"},
                ],
                "optional_addons": [],
                "refund_policy": {
                    "cutoff_hours": 72,
                    "full_refund_until": "활동 3일 전까지",
                    "note": "3~1일 전 50% / 당일 환불 불가",
                },
                "summary_line": f"참가비 {cost:,}원에 가이드와 진행이 포함되어 있어요.",
            }

        included = []
        for item in pb.get("included_items", []) or []:
            included.append({"name": item["name"], "value": item["value"]})
        if not included:
            included.append(
                {"name": "가이드/진행", "value": "호스트의 시간과 진행을 포함해요"}
            )

        addons: List[Dict[str, Any]] = []
        for a in pb.get("optional_addons", []) or []:
            mech = a.get("mechanism", "fixed")
            explanation = {
                "fixed": "참가비 외 정액으로 추가돼요",
                "funding": "정원이 모이면 다 같이 나눠 내요",
                "realcost": "실비라 영수증 공유해드려요",
            }.get(mech, "참가비 외 옵션이에요")
            addons.append(
                {
                    "name": a["name"],
                    "price": int(a["price"]),
                    "mechanism": mech,
                    "explanation": explanation,
                }
            )

        refund = pb.get("refund_policy")
        refund_obj = None
        if refund:
            refund_obj = {
                "cutoff_hours": int(refund["cutoff_hours"]),
                "full_refund_until": refund.get("full_refund_until"),
                "note": refund.get("note"),
            }

        base_fee = int(pb.get("base_fee", cost))
        summary = f"참가비 {base_fee:,}원, 포함 항목 {len(included)}개"
        if addons:
            summary += f", 선택 옵션 {len(addons)}개"
        summary += "이에요."

        return {
            "base_fee": base_fee,
            "included_items": included,
            "optional_addons": addons,
            "refund_policy": refund_obj,
            "summary_line": summary,
        }


__all__ = ["SpotPriceGenerator"]
