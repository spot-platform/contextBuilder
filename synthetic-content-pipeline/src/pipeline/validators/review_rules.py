"""Layer 2 — SpotReview 전용 rule validation.

구현 rule:
    1. rule_rating_sentiment_match        — rating → sentiment 강제 매핑
    2. rule_noshow_mention_consistency    — no_show_count>0 일 때 '전원'/'모두' 금지
    3. rule_will_rejoin_vs_rating         — rating==1 AND will_rejoin=True → warn
    4. rule_review_length_bucket_match    — meta.review_length_bucket 이 있으면 문장 수 버킷 체크
    5. rule_satisfaction_tags_range       — 1~5 개, 각 2~12 자
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from pipeline.spec.models import ContentSpec
from pipeline.validators.types import Rejection, ValidationResult

DEFAULT_RULES_DIR = Path("config/rules")

_SENTENCE_END_RE = re.compile(r"[.!?。！？]+")


def _count_sentences(text: str) -> int:
    if not text or not text.strip():
        return 0
    matches = _SENTENCE_END_RE.findall(text)
    if not matches:
        return 1
    return len(matches)


def load_review_rules(rules_dir: Optional[Path] = None) -> Dict[str, Any]:
    base = Path(rules_dir) if rules_dir else DEFAULT_RULES_DIR
    path = base / "review_rules.yaml"
    data: Dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    return {"review": data}


def _expected_sentiment_for(rating: int, mapping: Dict[str, str]) -> Optional[str]:
    """rating → sentiment. mapping 키는 str("1"~"5") 로 들어온다 (yaml 관례)."""
    if rating is None:
        return None
    return mapping.get(str(int(rating)))


# ---------------------------------------------------------------------------
# Rule 1. rating ↔ sentiment 매핑
# ---------------------------------------------------------------------------


def rule_rating_sentiment_match(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    cfg = rules.get("review") or {}
    mapping = cfg.get("rating_sentiment_map") or {
        "1": "negative",
        "2": "negative",
        "3": "neutral",
        "4": "positive",
        "5": "positive",
    }
    rating = payload.get("rating")
    sentiment = payload.get("sentiment")
    if not isinstance(rating, int) or not isinstance(sentiment, str):
        return []
    expected = _expected_sentiment_for(rating, mapping)
    if expected is None:
        return []
    if sentiment != expected:
        return [
            Rejection(
                layer="rule",
                rejected_field="review:sentiment",
                reason="rating_sentiment_mismatch",
                detail=(
                    f"rating={rating} 기준 sentiment 는 '{expected}' 이어야 하는데 "
                    f"'{sentiment}' 로 기록됨"
                ),
                instruction=(
                    f"sentiment 를 '{expected}' 로 교체하거나, "
                    f"rating 과 review_text 톤을 '{expected}' 로 일관되게 수정하라."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Rule 2. 노쇼 vs '전원 참여' 모순
# ---------------------------------------------------------------------------


def rule_noshow_mention_consistency(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    if spec.activity_result is None:
        return []
    if spec.activity_result.no_show_count <= 0:
        return []
    text = payload.get("review_text")
    if not isinstance(text, str):
        return []
    cfg = rules.get("review") or {}
    forbidden = cfg.get("forbidden_unanimous_terms") or []
    rejections: List[Rejection] = []
    for term in forbidden:
        if term and term in text:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field="review:review_text",
                    reason="noshow_contradiction",
                    detail=(
                        f"activity_result.no_show_count={spec.activity_result.no_show_count} 인데 "
                        f"review_text 에 '{term}' 표현 포함"
                    ),
                    instruction=(
                        f"'{term}' 표현을 빼고, 노쇼가 있었던 정황을 자연스럽게 반영하라 "
                        "(예: '두 분은 못 오셨지만', '작은 인원으로 더 편했어요')."
                    ),
                )
            )
    return rejections


# ---------------------------------------------------------------------------
# Rule 3. rating==1 AND will_rejoin True → warn
# ---------------------------------------------------------------------------


def rule_will_rejoin_vs_rating(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    rating = payload.get("rating")
    will_rejoin = payload.get("will_rejoin")
    if rating == 1 and will_rejoin is True:
        return [
            Rejection(
                layer="rule",
                rejected_field="review:will_rejoin",
                reason="will_rejoin_contradicts_rating",
                detail="rating=1 인데 will_rejoin=True — 재참여 의향이 어색",
                instruction=(
                    "rating=1 리뷰어라면 will_rejoin 을 False 로 바꾸거나, "
                    "review_text 와 rating 을 상향 조정해 톤을 일관되게 맞춰라."
                ),
                severity="warn",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Rule 4. review_length_bucket 버킷 일치 (meta 있을 때만)
# ---------------------------------------------------------------------------


def rule_review_length_bucket_match(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return []
    bucket = meta.get("review_length_bucket")
    if not isinstance(bucket, str):
        return []
    cfg = rules.get("review") or {}
    bucket_map = cfg.get("length_bucket_sentences") or {}
    rng = bucket_map.get(bucket)
    if not isinstance(rng, list) or len(rng) != 2:
        return []
    lo, hi = int(rng[0]), int(rng[1])
    text = payload.get("review_text")
    if not isinstance(text, str):
        return []
    n = _count_sentences(text)
    if n < lo or n > hi:
        return [
            Rejection(
                layer="rule",
                rejected_field="review:review_text",
                reason="review_length_bucket_mismatch",
                detail=(
                    f"review_length_bucket='{bucket}' 범위 {lo}~{hi} 문장인데 실제 {n} 문장"
                ),
                instruction=(
                    f"review_text 를 {lo}~{hi} 문장에 맞춰 다시 작성하라."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Rule 5. satisfaction_tags 개수/길이
# ---------------------------------------------------------------------------


def rule_satisfaction_tags_range(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    cfg = rules.get("review") or {}
    min_n = int(cfg.get("tag_min_count", 1))
    max_n = int(cfg.get("tag_max_count", 5))
    min_len = int(cfg.get("tag_min_length", 2))
    max_len = int(cfg.get("tag_max_length", 12))

    tags = payload.get("satisfaction_tags")
    if not isinstance(tags, list):
        return []
    rejections: List[Rejection] = []
    if len(tags) < min_n or len(tags) > max_n:
        rejections.append(
            Rejection(
                layer="rule",
                rejected_field="review:satisfaction_tags",
                reason="satisfaction_tags_count_out_of_range",
                detail=f"satisfaction_tags 개수 {len(tags)} 가 {min_n}~{max_n} 범위 밖",
                instruction=(
                    f"satisfaction_tags 를 {min_n}~{max_n} 개로 조정하라."
                ),
            )
        )
    for idx, tag in enumerate(tags):
        if not isinstance(tag, str):
            continue
        if len(tag) < min_len or len(tag) > max_len:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field=f"review:satisfaction_tags[{idx}]",
                    reason="satisfaction_tag_length",
                    detail=f"tag='{tag}' 길이 {len(tag)} 가 {min_len}~{max_len} 범위 밖",
                    instruction=(
                        f"'{tag}' 를 {min_len}~{max_len}자 길이로 재작성하라."
                    ),
                )
            )
    return rejections


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

_REVIEW_RULE_FUNCTIONS = (
    rule_rating_sentiment_match,
    rule_noshow_mention_consistency,
    rule_will_rejoin_vs_rating,
    rule_review_length_bucket_match,
    rule_satisfaction_tags_range,
)


def validate_review_rules(
    payload: Dict[str, Any],
    spec: ContentSpec,
    *,
    rules: Optional[Dict[str, Any]] = None,
    rules_dir: Optional[Path] = None,
) -> ValidationResult:
    if rules is None:
        rules = load_review_rules(rules_dir)

    all_rejections: List[Rejection] = []
    rule_stats: Dict[str, int] = {}
    for fn in _REVIEW_RULE_FUNCTIONS:
        out = fn(payload, spec, rules)
        rule_stats[fn.__name__] = len(out)
        all_rejections.extend(out)

    meta = {
        "rule_stats": rule_stats,
        "rating": payload.get("rating"),
        "sentiment": payload.get("sentiment"),
    }
    return ValidationResult.from_rejections("rule", all_rejections, meta=meta)


__all__ = [
    "validate_review_rules",
    "load_review_rules",
    "rule_rating_sentiment_match",
    "rule_noshow_mention_consistency",
    "rule_will_rejoin_vs_rating",
    "rule_review_length_bucket_match",
    "rule_satisfaction_tags_range",
]
