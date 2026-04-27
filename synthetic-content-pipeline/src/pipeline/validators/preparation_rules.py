"""Layer 2 — preparation rule validation (Phase 4d).

JSON schema 가 길이를 잡으므로 여기는:
- 강제 톤 어휘 ("필수 지참", "지참 시 권장", "입장 제한") 금지.
- AI틱 어휘 블랙리스트.
- venue=park 인데 weather_contingency 가 비면 warn (hard reject 아님).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pipeline.spec.models import ContentSpec
from pipeline.validators.types import Rejection, ValidationResult

_FORBIDDEN_TERMS = (
    "조율", "밸런스", "큐레이션", "정수", "묘미", "본격", "완벽한",
    "필수 지참", "필수지참", "지참 시 권장", "미흡 시", "입장 제한",
)


def load_preparation_rules(rules_dir: Optional[Path] = None) -> Dict[str, Any]:
    return {}


def validate_preparation_rules(
    payload: Dict[str, Any],
    spec: ContentSpec,
    *,
    rules: Optional[Dict[str, Any]] = None,
) -> ValidationResult:
    rejections: List[Rejection] = []

    text_blob = " ".join(
        [
            *[str(s) for s in payload.get("host_provides") or []],
            *[str(s) for s in payload.get("partner_brings") or []],
            *[str(s) for s in payload.get("safety_notes") or []],
            str(payload.get("weather_contingency") or ""),
            str(payload.get("host_tip") or ""),
        ]
    )
    for term in _FORBIDDEN_TERMS:
        if term in text_blob:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="preparation:text",
                    reason="forbidden_term",
                    detail=f"forbidden term: {term}",
                    instruction=f"'{term}' 어휘 제거",
                    severity="reject",
                )
            )
            break

    # park venue 인데 weather_contingency 누락 → warn
    venue = (spec.venue_type or "").lower()
    if venue == "park" and not payload.get("weather_contingency"):
        rejections.append(
            Rejection(
                layer="rule",
                rejected_field="preparation:weather_contingency",
                reason="missing_weather_contingency",
                detail="venue=park 인데 weather_contingency 미작성",
                instruction="우천/혹서 대비 한 줄 추가 권장",
                severity="warn",
            )
        )

    return ValidationResult.from_rejections("rule", rejections)


__all__ = ["load_preparation_rules", "validate_preparation_rules"]
