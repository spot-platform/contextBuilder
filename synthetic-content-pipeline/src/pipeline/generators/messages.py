"""MessagesGenerator — Job 4 (§3-D, §11).

ContentSpec → 커뮤니케이션 snippet 4종 (모집소개/참여승인/당일안내/감사) × 2 후보.

schema: ``src/pipeline/llm/schemas/messages.json``
프롬프트: ``config/prompts/messages/v1.j2``

4개의 snippet 을 **한 번의 LLM 호출**로 생성한다. 같은 스팟·같은 호스트의 발화이므로
톤/지역/시간에 대한 일관성을 프롬프트에서 강조한다.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict

from pipeline.generators.base import BaseGenerator
from pipeline.generators.persona_tones import tone_examples_for
from pipeline.spec.models import ContentSpec


class MessagesGenerator(BaseGenerator):
    """4종 snippet 통합 생성기."""

    content_type: str = "messages"
    template_id: str = "messages:v2"
    template_path: str = "messages/v2.j2"
    schema_path: Path = (
        Path(__file__).resolve().parent.parent / "llm" / "schemas" / "messages.json"
    )

    def __init__(self) -> None:
        if not self.schema_path.exists():
            warnings.warn(
                f"{self.template_id}: schema not found at {self.schema_path} — "
                "bridge must resolve before live mode.",
                stacklevel=2,
            )

    def spec_to_variables(
        self,
        spec: ContentSpec,
        *,
        variant: str,
        length_bucket: str,
    ) -> Dict[str, Any]:
        variables = super().spec_to_variables(
            spec, variant=variant, length_bucket=length_bucket
        )
        variables["tone_examples"] = tone_examples_for(spec.host_persona.type)
        variables["host_trust_level"] = (
            "trusted" if spec.activity_constraints.supporter_required else "neutral"
        )
        # 모집 상태: Phase 1 에선 recruiting 고정 (activity_result 가 있으면 closed).
        variables["recruit_status"] = (
            "closed" if spec.activity_result is not None else "recruiting"
        )
        return variables

    def _placeholder_payload(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """stub 폴백 — 4종 snippet 동시 반환."""
        region = variables["region_label"]
        time_str = variables["schedule_time"]
        date = variables["schedule_date"]
        people = variables["participants_expected_count"]

        return {
            "recruiting_intro": (
                f"{region} 에서 {date} {time_str} 에 {people}명 규모 가벼운 모임을 모집해요. "
                "초면도 환영이고, 편한 마음으로 신청 부탁드려요. 자리 정해지면 확정 안내 드릴게요."
            ),
            "join_approval": (
                f"신청 감사드립니다. {date} {time_str} {region} 에서 뵐게요. 자리 확정됐어요."
            ),
            "day_of_notice": (
                f"오늘 {time_str} {region} 에서 만나요. 이동 중 지연 있으면 편히 알려주시고, "
                "편한 차림으로 오시면 됩니다."
            ),
            "post_thanks": (
                f"오늘 {region} 모임 함께해 주셔서 고맙습니다. 다음에 또 편하게 뵐 수 있길 바라요."
            ),
        }


__all__ = ["MessagesGenerator"]
