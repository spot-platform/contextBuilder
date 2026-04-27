"""Layer 2 — price rule validation (Phase 4c).

JSON schema 가 이미 대부분의 구조를 잡으므로 여기는:
- base_fee 가 spec.budget.expected_cost_per_person ±10% 인지 확인.
- optional_addons[].mechanism 이 enum 안인지는 schema 가 처리.
- AI틱 / 마케팅 어휘 블랙리스트 (간단).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pipeline.spec.models import ContentSpec
from pipeline.validators.types import Rejection, ValidationResult

_FORBIDDEN_TERMS = (
    "조율", "밸런스", "큐레이션", "정수", "묘미", "본격", "진정한", "완벽한",
    "프리미엄", "VIP", "마진",
)

_DEFAULT_RULES: Dict[str, Any] = {
    "base_fee_tolerance_pct": 10.0,
}


def load_price_rules(rules_dir: Optional[Path] = None) -> Dict[str, Any]:
    return dict(_DEFAULT_RULES)


def validate_price_rules(
    payload: Dict[str, Any],
    spec: ContentSpec,
    *,
    rules: Optional[Dict[str, Any]] = None,
) -> ValidationResult:
    rules = rules or _DEFAULT_RULES
    rejections: List[Rejection] = []

    base_fee = int(payload.get("base_fee", 0))
    expected = int(spec.budget.expected_cost_per_person or 0)
    tol = float(rules.get("base_fee_tolerance_pct", 10.0)) / 100.0
    if expected > 0:
        low = expected * (1 - tol)
        high = expected * (1 + tol)
        if not (low <= base_fee <= high):
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="price:base_fee",
                    reason="base_fee_out_of_range",
                    detail=f"base_fee={base_fee} outside ±{tol*100:.0f}% of {expected}",
                    instruction=f"base_fee 를 {expected}원 근처로 조정",
                    severity="reject",
                )
            )

    # 금기어 — summary_line / explanation 검사
    text_blob = " ".join(
        [
            str(payload.get("summary_line") or ""),
            *[
                str(a.get("explanation") or "")
                for a in payload.get("optional_addons") or []
            ],
            *[str(i.get("value") or "") for i in payload.get("included_items") or []],
        ]
    )
    for term in _FORBIDDEN_TERMS:
        if term in text_blob:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="price:text",
                    reason="forbidden_term",
                    detail=f"forbidden term: {term}",
                    instruction=f"'{term}' 어휘 제거",
                    severity="reject",
                )
            )
            break  # 한 번만

    return ValidationResult.from_rejections("rule", rejections)


__all__ = ["load_price_rules", "validate_price_rules"]
