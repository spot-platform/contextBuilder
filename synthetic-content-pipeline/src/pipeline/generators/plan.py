"""SpotPlanGenerator — Job 3 후반부 (§3-C, §11).

ContentSpec → 활동 타임라인 × 2 후보.

schema: ``src/pipeline/llm/schemas/plan.json``
프롬프트: ``config/prompts/plan/v2.j2``

스팟 단위 cross-reference (Layer 3) 에서 총 소요 시간이 spec.schedule.duration_minutes
와 일치해야 하므로, generator 가 결정성 타임라인 초안을 만들어 프롬프트에 주입한다.
LLM 은 activity 문구를 자연어로 다듬는 역할만 수행한다.
"""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

from pipeline.generators.base import BaseGenerator
from pipeline.generators.persona_tones import tone_examples_for
from pipeline.spec.models import ContentSpec


def _shift_time(start_time: str, offset_minutes: int) -> str:
    """HH:MM + offset → HH:MM (24h wrap)."""
    base = datetime.strptime(start_time, "%H:%M")
    shifted = base + timedelta(minutes=offset_minutes)
    return shifted.strftime("%H:%M")


def _build_plan_draft(start_time: str, duration_minutes: int) -> List[Dict[str, str]]:
    """결정성 5-step 초안.

    - 0분: 시작/인사
    - 15분: 아이스브레이킹
    - 30분: 메인 활동 시작
    - duration-20분: 정리
    - duration-10분: 후기/감사
    """
    # duration 이 너무 짧으면 step 수 줄임.
    if duration_minutes < 60:
        offsets = [0, 10, 20, max(duration_minutes - 10, 30)]
        labels = [
            "시작 및 인사",
            "아이스브레이킹",
            "본 활동 시작",
            "마무리 및 후기",
        ]
    else:
        offsets = [
            0,
            15,
            30,
            max(duration_minutes - 20, 40),
            max(duration_minutes - 10, 50),
        ]
        labels = [
            "집결 및 인사",
            "아이스브레이킹",
            "메인 활동 시작",
            "정리 및 소감",
            "후기 공유 및 해산",
        ]
    # 오프셋이 증가해야 함 (wrap 방지).
    cleaned: List[Dict[str, str]] = []
    last_offset = -1
    for offset, label in zip(offsets, labels):
        if offset <= last_offset:
            offset = last_offset + 5
        cleaned.append(
            {"time": _shift_time(start_time, offset), "activity_hint": label}
        )
        last_offset = offset
    return cleaned


class SpotPlanGenerator(BaseGenerator):
    """스팟 타임라인 생성기."""

    content_type: str = "plan"
    template_id: str = "plan:v2"
    template_path: str = "plan/v2.j2"
    schema_path: Path = (
        Path(__file__).resolve().parent.parent / "llm" / "schemas" / "plan.json"
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
        variables["schedule_duration_minutes"] = spec.schedule.duration_minutes
        variables["plan_draft"] = _build_plan_draft(
            spec.schedule.start_time, spec.schedule.duration_minutes
        )
        return variables

    def _placeholder_payload(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """stub 폴백 — draft 를 그대로 plan steps 로 변환."""
        draft = variables.get("plan_draft") or []
        steps = [
            {"time": row["time"], "activity": row["activity_hint"]}
            for row in draft
        ]
        # 최소 3 step 확보.
        while len(steps) < 3:
            steps.append(
                {"time": variables["schedule_time"], "activity": "본 활동 진행"}
            )
        return {
            "steps": steps,
            "total_duration_minutes": int(
                variables.get(
                    "schedule_duration_minutes",
                    variables.get("activity_result", {}).get(
                        "duration_actual_minutes", 120
                    )
                    if isinstance(variables.get("activity_result"), dict)
                    else 120,
                )
            ),
        }


__all__ = ["SpotPlanGenerator"]
