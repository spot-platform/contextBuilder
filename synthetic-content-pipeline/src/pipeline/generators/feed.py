"""FeedGenerator — Job 2 (§11). ContentSpec → feed preview × 2 후보.

스키마(예정): config 외부 `src/pipeline/llm/schemas/feed.json` (codex-bridge 가 enforce).
이 generator 는 schema_path 만 가리키고, 실제 검증/재시도는 bridge 에 위임한다.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List

from pipeline.generators.base import BaseGenerator, normalize_region_label
from pipeline.generators.persona_tones import tone_examples_for
from pipeline.spec.models import ContentSpec


# §A — 오프닝 angle 풀. spot_id × variant deterministic seed 로 선택.
# 각 angle 은 feed/v2.j2 가 분기 처리한다.
_OPENING_ANGLES: List[str] = [
    "ask",
    "confession",
    "scene",
    "invitation",
    "detail_lead",
    "contrast",
]


def _pick_opening_angle(spot_id: str, variant: str) -> str:
    """deterministic 한 angle 선택. variant 가 다르면 다른 angle 이 우선 시도된다."""
    rng = random.Random(hash((spot_id, variant, "open_angle")) & 0xFFFFFFFF)
    return rng.choice(_OPENING_ANGLES)


# 가격 라벨용 단위 변환 (참고용 — 프롬프트 본문이 직접 표현).
def _format_price_label(cost_per_person: int) -> str:
    """예산 → 한국어 라벨. ex) 18000 → '1인 약 1.8만원'."""
    if cost_per_person <= 0:
        return "1인 무료"
    if cost_per_person >= 10_000:
        return f"1인 약 {cost_per_person / 10_000:.1f}만원".replace(".0만원", "만원")
    return f"1인 약 {cost_per_person:,}원"


def _format_time_label(date_iso: str, start_time: str) -> str:
    """ISO date+time → '4/18(금) 19:00' 한국어 라벨."""
    from datetime import datetime

    dt = datetime.strptime(date_iso, "%Y-%m-%d")
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return f"{dt.month}/{dt.day}({weekdays[dt.weekday()]}) {start_time}"


class FeedGenerator(BaseGenerator):
    """피드 카드 생성기."""

    content_type: str = "feed"
    template_id: str = "feed:v2"
    template_path: str = "feed/v2.j2"
    # `src/pipeline/llm/schemas/feed.json` — bridge 가 만들 위치.
    schema_path: Path = (
        Path(__file__).resolve().parent.parent / "llm" / "schemas" / "feed.json"
    )

    def spec_to_variables(
        self,
        spec: ContentSpec,
        *,
        variant: str,
        length_bucket: str,
    ) -> Dict[str, Any]:
        """공용 변수 + feed 전용 hint."""
        variables = super().spec_to_variables(
            spec, variant=variant, length_bucket=length_bucket
        )
        # feed 전용 보조 변수 — prompt 본문이 참조 (공용 표준 외 영역).
        variables["tone_examples"] = tone_examples_for(spec.host_persona.type)
        variables["price_label_hint"] = _format_price_label(
            spec.budget.expected_cost_per_person
        )
        variables["time_label_hint"] = _format_time_label(
            spec.schedule.date, spec.schedule.start_time
        )
        variables["supporter_label_hint"] = spec.host_persona.type
        # §A — 오프닝 angle 주입. spot_id × variant deterministic.
        variables["opening_angle"] = _pick_opening_angle(spec.spot_id, variant)
        return variables

    def _placeholder_payload(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """stub / bridge 미완성 폴백 시 그럴듯한 feed payload 반환.

        validator-engineer 와 pipeline-qa 가 boundary 를 점검할 수 있도록
        실제 운영 schema 와 동일한 키 집합을 채운다.
        """
        region = variables["region_label"]
        category = variables["category"]
        slot = variables["schedule_time_slot"]
        cost = variables["budget_cost_per_person"]
        people = variables["participants_expected_count"]

        title = f"{region} {slot} 모임, {people}명 모집"
        if len(title) < 12:
            title = f"{region} {category} 모임 {people}명 모집"
        if len(title) > 40:
            title = title[:40]

        summary = (
            f"{region}에서 {slot} 시간대에 {category} 모임을 가볍게 진행해요. "
            f"예상 1인 약 {cost:,}원, 처음 오시는 분도 환영합니다."
        )

        return {
            "title": title,
            "summary": summary,
            "tags": [region.split()[-1], category, slot, "초면환영"],
            "price_label": variables.get("price_label_hint", f"1인 약 {cost:,}원"),
            "region_label": region,
            "time_label": variables.get(
                "time_label_hint",
                f"{variables['schedule_date']} {variables['schedule_time']}",
            ),
            "status": "recruiting",
            "supporter_label": variables["host_persona"]["type"],
        }


__all__ = ["FeedGenerator"]
