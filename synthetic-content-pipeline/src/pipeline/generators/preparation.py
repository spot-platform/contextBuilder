"""SpotPreparationGenerator — Phase 4d (POI-anchored).

ContentSpec.preparation (deterministic draft) 를 받아 자연어 표현만 LLM 이
다듬고 host_tip 한 줄을 추가. host_provides / partner_brings 는 가능한 그대로.

schema: ``src/pipeline/llm/schemas/preparation_v1.json``
프롬프트: ``config/prompts/preparation/v1.j2``
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict, List

from pipeline.generators.base import BaseGenerator


class SpotPreparationGenerator(BaseGenerator):
    """준비물 가이드 생성기."""

    content_type: str = "preparation"
    template_id: str = "preparation:v1"
    template_path: str = "preparation/v1.j2"
    schema_path: Path = (
        Path(__file__).resolve().parent.parent
        / "llm"
        / "schemas"
        / "preparation_v1.json"
    )

    def __init__(self) -> None:
        if not self.schema_path.exists():
            warnings.warn(
                f"{self.template_id}: schema not found at {self.schema_path}",
                stacklevel=2,
            )

    def _placeholder_payload(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """stub 폴백 — spec.preparation 그대로 schema 형태."""
        prep = variables.get("preparation")
        skill = variables.get("skill_topic") or variables.get("category") or "활동"
        if not prep:
            return {
                "host_provides": ["진행 자료"],
                "partner_brings": ["편하게 오시면 됩니다"],
                "weather_contingency": None,
                "safety_notes": [],
                "host_tip": f"{skill} 처음이신 분도 편하게 오셔도 좋아요.",
            }

        host_provides: List[str] = list(prep.get("host_provides") or [])
        partner_brings: List[str] = list(prep.get("partner_brings") or [])
        weather = prep.get("weather_contingency")
        safety = list(prep.get("safety_notes") or [])

        if partner_brings:
            tip = f"{partner_brings[0]} 정도만 챙겨 오시면 됩니다."
        else:
            tip = f"{skill} 처음이신 분도 편하게 오셔도 좋아요."

        return {
            "host_provides": host_provides,
            "partner_brings": partner_brings,
            "weather_contingency": weather,
            "safety_notes": safety,
            "host_tip": tip,
        }


__all__ = ["SpotPreparationGenerator"]
