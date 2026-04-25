"""SpotDetailGenerator — Job 3 전반부 (§3-B, §11).

ContentSpec → 상세 페이지 콘텐츠 × 2 후보.

schema 파일: ``src/pipeline/llm/schemas/detail.json``
프롬프트: ``config/prompts/detail/v2.j2``

Phase 2 cross-reference validator 가 스팟 단위로 feed ↔ detail 일치(지역/인원/금액/카테고리)
를 체크하므로, 프롬프트에서 사실 일관성을 hard rule 로 강제한다.
"""
from __future__ import annotations

import random
import warnings
from pathlib import Path
from typing import Any, Dict, List

from pipeline.generators.base import BaseGenerator, _seeded_random
from pipeline.generators.persona_tones import tone_examples_for
from pipeline.spec.models import ContentSpec


# §7-2 자연스러움 전략: 준비물 기재율 분포.
_MATERIALS_BUCKETS: List[tuple[str, float]] = [
    ("none", 0.40),
    ("simple", 0.35),
    ("detailed", 0.25),
]

# §7-2: 소개(description) 길이 분포 (30 / 50 / 20).
_DESCRIPTION_LENGTH_BUCKETS: List[tuple[str, float]] = [
    ("short", 0.30),
    ("medium", 0.50),
    ("long", 0.20),
]


def _sample_from(dist: List[tuple[str, float]], rng: random.Random) -> str:
    roll = rng.random()
    cumulative = 0.0
    for name, weight in dist:
        cumulative += weight
        if roll <= cumulative:
            return name
    return dist[-1][0]


def _sample_materials_bucket(spot_id: str) -> str:
    rng = _seeded_random(spot_id, "materials")
    return _sample_from(_MATERIALS_BUCKETS, rng)


def _sample_description_length_bucket(spot_id: str) -> str:
    rng = _seeded_random(spot_id, "description_length")
    return _sample_from(_DESCRIPTION_LENGTH_BUCKETS, rng)


class SpotDetailGenerator(BaseGenerator):
    """상세 페이지 생성기."""

    content_type: str = "detail"
    template_id: str = "detail:v2"
    template_path: str = "detail/v2.j2"
    schema_path: Path = (
        Path(__file__).resolve().parent.parent / "llm" / "schemas" / "detail.json"
    )

    def __init__(self) -> None:
        # schema 파일 존재 여부만 경고. call_codex 시점엔 bridge 가 처리.
        if not self.schema_path.exists():
            warnings.warn(
                f"{self.template_id}: schema not found at {self.schema_path} — "
                "falling back to feed.json; bridge must resolve before live mode.",
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
        # detail 전용 보조 변수.
        variables["tone_examples"] = tone_examples_for(spec.host_persona.type)
        variables["materials_bucket"] = _sample_materials_bucket(spec.spot_id)
        variables["description_length_bucket"] = _sample_description_length_bucket(
            spec.spot_id
        )
        return variables

    def _placeholder_payload(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """stub / bridge 미완성 폴백. detail 스키마 키 집합과 동일."""
        region = variables["region_label"]
        category = variables["category"]
        people = variables["participants_expected_count"]
        cost = variables["budget_cost_per_person"]

        title = f"{region} {category} 모임 {people}명 상세 안내"
        if len(title) < 12:
            title = f"{region} {category} 소규모 모임 {people}명"
        if len(title) > 60:
            title = title[:60]

        description = (
            f"{region}에서 {people}명 규모로 {category} 모임을 진행해요. "
            "편안한 분위기에서 가볍게 인사 나누고, 준비된 순서대로 진행됩니다. "
            "초면이어도 부담 없이 참여할 수 있도록 호스트가 진행을 도와드려요."
        )

        materials_bucket = variables.get("materials_bucket", "simple")
        if materials_bucket == "none":
            materials: List[str] = []
        elif materials_bucket == "simple":
            materials = ["편한 복장"]
        else:
            materials = ["편한 복장", "개인 텀블러", "필기도구"]

        return {
            "title": title,
            "description": description,
            "activity_purpose": (
                f"{region} 이웃과 {category} 를 통해 가볍게 친해지는 것"
            ),
            "progress_style": (
                f"{variables['schedule_time']} 집결 → 인사 → 본 활동 → 마무리 공유. "
                "호스트가 어색한 공백 없이 진행을 이끌어요."
            ),
            "materials": materials,
            "target_audience": f"{region} 거주/직장 중 초면 모임이 괜찮은 분",
            "cost_breakdown": [
                {"item": "참가비 (1인)", "amount": int(cost)},
            ],
            "host_intro": (
                f"{region} 에서 활동 중인 {variables['host_persona']['type']} 호스트예요. "
                "처음 오시는 분도 금방 편해지실 수 있게 도와드릴게요."
            ),
            "policy_notes": "당일 노쇼는 다음 모집부터 참여가 제한될 수 있어요.",
        }


__all__ = ["SpotDetailGenerator"]
