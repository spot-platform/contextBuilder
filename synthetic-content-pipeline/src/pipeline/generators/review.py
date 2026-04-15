"""ReviewGenerator — Job 5 (§3-E, §11).

ContentSpec → 리뷰(별점 + 자유 텍스트 + 태그) × 2 후보.

schema: ``src/pipeline/llm/schemas/review.json``
프롬프트: ``config/prompts/review/v1.j2``

별점 분포 (§7-3) 를 activity_result.overall_sentiment 에 따라 편향 샘플링하고
리뷰 길이 분포 (§7-2) 를 결정한 뒤, 프롬프트에서 rating ↔ sentiment 정합성을
hard rule 로 강제한다.
"""
from __future__ import annotations

import json
import random
import warnings
from pathlib import Path
from typing import Any, Dict, List

from pipeline.generators.base import BaseGenerator, _seeded_random
from pipeline.generators.persona_tones import tone_examples_for
from pipeline.spec.models import ContentSpec


# §7-3 sentiment 별 별점 분포 (task 지시 사항과 일치).
_RATING_DISTRIBUTIONS: Dict[str, Dict[int, float]] = {
    "positive": {5: 0.55, 4: 0.30, 3: 0.10, 2: 0.03, 1: 0.02},
    "neutral":  {5: 0.25, 4: 0.35, 3: 0.25, 2: 0.10, 1: 0.05},
    "negative": {5: 0.05, 4: 0.15, 3: 0.30, 2: 0.30, 1: 0.20},
}

# §7-2 리뷰 길이 분포.
_REVIEW_LENGTH_BUCKETS: List[tuple[str, float]] = [
    ("short", 0.25),
    ("medium", 0.50),
    ("long", 0.25),
]


def _sample_rating(spot_id: str, variant: str, sentiment: str) -> int:
    rng = _seeded_random(spot_id, variant, "rating")
    dist = _RATING_DISTRIBUTIONS.get(sentiment, _RATING_DISTRIBUTIONS["neutral"])
    roll = rng.random()
    cumulative = 0.0
    for rating, weight in sorted(dist.items(), key=lambda kv: -kv[0]):  # 5→1 순
        cumulative += weight
        if roll <= cumulative:
            return rating
    return 3


def _sample_review_length(spot_id: str, variant: str) -> str:
    rng = _seeded_random(spot_id, variant, "review_length")
    roll = rng.random()
    cumulative = 0.0
    for name, weight in _REVIEW_LENGTH_BUCKETS:
        cumulative += weight
        if roll <= cumulative:
            return name
    return _REVIEW_LENGTH_BUCKETS[-1][0]


def _sentiment_from_rating(rating: int) -> str:
    if rating >= 4:
        return "positive"
    if rating == 3:
        return "neutral"
    return "negative"


class ReviewGenerator(BaseGenerator):
    """리뷰 생성기."""

    content_type: str = "review"
    template_id: str = "review:v2"
    template_path: str = "review/v2.j2"
    schema_path: Path = (
        Path(__file__).resolve().parent.parent / "llm" / "schemas" / "review.json"
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
        # 기본 sentiment — settle 전이면 neutral 로 가정.
        sentiment = (
            spec.activity_result.overall_sentiment
            if spec.activity_result is not None
            else "neutral"
        )
        target_rating = _sample_rating(spec.spot_id, variant, sentiment)
        target_sentiment = _sentiment_from_rating(target_rating)

        issues_context: List[str] = []
        noshow_happened = False
        actual_participants = spec.participants.expected_count
        no_show_count = 0
        if spec.activity_result is not None:
            issues_context = list(spec.activity_result.issues)
            no_show_count = int(spec.activity_result.no_show_count)
            noshow_happened = no_show_count > 0
            actual_participants = int(spec.activity_result.actual_participants)

        variables["tone_examples"] = tone_examples_for(spec.host_persona.type)
        variables["target_rating"] = int(target_rating)
        variables["target_sentiment"] = target_sentiment
        variables["review_length_bucket"] = _sample_review_length(
            spec.spot_id, variant
        )
        variables["issues_context"] = issues_context
        variables["noshow_happened"] = noshow_happened
        variables["no_show_count"] = no_show_count
        variables["actual_participants"] = actual_participants
        return variables

    def _placeholder_payload(self, variables: Dict[str, Any]) -> Dict[str, Any]:
        """stub 폴백 — rating/sentiment 정합 payload."""
        region = variables["region_label"]
        rating = int(variables.get("target_rating", 4))
        sentiment = variables.get("target_sentiment", _sentiment_from_rating(rating))
        length_bucket = variables.get("review_length_bucket", "medium")

        positive_text = (
            f"{region} 모임 분위기가 편안해서 좋았어요. "
            "호스트분이 잘 이끌어 주셔서 초면인데도 금방 편해졌습니다. "
            "다음에도 기회가 되면 또 참여해 볼 생각이에요."
        )
        neutral_text = (
            f"{region} 모임은 무난하게 다녀왔어요. "
            "특별히 좋거나 아쉬웠던 것은 없었고, 기대한 만큼의 자리였습니다."
        )
        negative_text = (
            f"{region} 모임은 기대보다는 조금 아쉬웠어요. "
            "시작이 살짝 늦었고 분위기가 제 취향과는 달랐습니다. "
            "다음 모임은 조금 더 신중히 고를 것 같아요."
        )
        review_text = {
            "positive": positive_text,
            "neutral": neutral_text,
            "negative": negative_text,
        }[sentiment]

        if length_bucket == "short":
            review_text = review_text.split(". ")[0] + "."
        elif length_bucket == "long":
            review_text = review_text + " 전반적으로 또래/이웃과 가볍게 시간 보내기 좋은 자리였어요."

        # 노쇼 언급 — neutral/negative 한정.
        if variables.get("noshow_happened") and sentiment != "positive":
            review_text = review_text + " 노쇼 인원이 있어서 분위기가 살짝 가라앉기도 했어요."

        satisfaction_tags_by_sent = {
            "positive": ["분위기좋음", "호스트친절", "재참여의사"],
            "neutral": ["무난함", "동네모임"],
            "negative": ["아쉬움", "기대이하"],
        }

        recommend = rating >= 4
        will_rejoin = rating >= 4

        return {
            "rating": rating,
            "review_text": review_text,
            "satisfaction_tags": satisfaction_tags_by_sent[sentiment],
            "recommend": recommend,
            "will_rejoin": will_rejoin,
            "sentiment": sentiment,
        }


__all__ = ["ReviewGenerator"]
