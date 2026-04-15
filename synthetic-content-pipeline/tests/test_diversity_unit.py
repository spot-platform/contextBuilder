"""Phase 3 — diversity.py 단위 테스트.

`pipeline.validators.diversity.compute_diversity` 의 4 가지 경계를 검증:

1. 동일 텍스트 후보 2 개 → diversity_score 가 낮다 (≤ 0.5).
2. 완전히 다른 텍스트 후보 2 개 → diversity_score 가 높다 (≥ 0.8).
3. approved_cache 에 동일 텍스트가 존재하면 후보 점수가 더 낮아진다.
4. 템플릿 패턴 ("가볍게 OO하면서 OO 나누는") 이 매치되면 감점된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

import pytest

from pipeline.validators.diversity import (
    compute_diversity,
    extract_text,
    load_diversity_patterns,
)


@dataclass
class _StubCandidate:
    """Candidate 와 같은 attribute (variant / payload / meta) 만 충족하는 stub."""

    variant: str
    payload: Mapping[str, Any]
    meta: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. 동일 텍스트 후보 2 개
# ---------------------------------------------------------------------------


def test_identical_candidates_have_low_diversity_score():
    payload = {"title": "수원에서 즐거운 점심", "summary": "동네 사람들과 식사해요"}
    a = _StubCandidate(variant="A", payload=payload, meta={"seed_hash": "aaa1"})
    b = _StubCandidate(variant="B", payload=payload, meta={"seed_hash": "bbb2"})

    scores = compute_diversity([a, b], "feed", approved_cache=())

    assert set(scores.keys()) == {"aaa1", "bbb2"}
    # 동일 텍스트는 ngram_jaccard / tfidf_cos 모두 1.0 → diversity = 0.0
    for cid, s in scores.items():
        assert s <= 0.5, f"identical text but {cid} score={s}"


# ---------------------------------------------------------------------------
# 2. 완전히 다른 텍스트 후보 2 개
# ---------------------------------------------------------------------------


def test_completely_different_candidates_have_high_diversity_score():
    a = _StubCandidate(
        variant="A",
        payload={
            "title": "수원에서 즐거운 점심",
            "summary": "동네 사람들과 한식 식사",
        },
        meta={"seed_hash": "aaa1"},
    )
    b = _StubCandidate(
        variant="B",
        payload={
            "title": "한강에서 자전거 타기",
            "summary": "운동 좋아하는 분들 모집",
        },
        meta={"seed_hash": "bbb2"},
    )

    scores = compute_diversity([a, b], "feed", approved_cache=())
    for cid, s in scores.items():
        # 완전 다른 텍스트는 점수가 0.8 이상이어야 한다.
        assert s >= 0.8, f"different text but {cid} score={s}"


# ---------------------------------------------------------------------------
# 3. approved_cache 효과
# ---------------------------------------------------------------------------


def test_approved_cache_with_duplicate_lowers_score():
    """동일 텍스트가 approved_cache 에 있으면 후보의 점수는 낮아져야 한다."""
    payload = {"title": "조용한 동네 카페 모임", "summary": "주말 오후에 모여요"}

    cand = _StubCandidate(
        variant="A",
        payload={
            "title": "활기찬 야시장 탐방",
            "summary": "수원 야시장 미식 투어",
        },
        meta={"seed_hash": "uniq1"},
    )
    other = _StubCandidate(
        variant="B",
        payload={
            "title": "한적한 호숫가 산책",
            "summary": "월요일 새벽 호수 둘레길 한 바퀴",
        },
        meta={"seed_hash": "uniq2"},
    )

    cache_match = _StubCandidate(
        variant="cache",
        payload={
            "title": "활기찬 야시장 탐방",
            "summary": "수원 야시장 미식 투어",
        },
        meta={"seed_hash": "cache_dup"},
    )

    no_cache = compute_diversity([cand, other], "feed", approved_cache=())
    with_cache = compute_diversity(
        [cand, other], "feed", approved_cache=[cache_match]
    )

    base = no_cache["uniq1"]
    cached = with_cache["uniq1"]
    assert cached <= base, (
        f"approved_cache duplicate should lower score: base={base}, cached={cached}"
    )
    # 동일 cache 항목이 있으니 cached 점수는 0.5 이하여야 한다.
    assert cached <= 0.5


# ---------------------------------------------------------------------------
# 4. 템플릿 패턴 매치 → 감점
# ---------------------------------------------------------------------------


def test_template_pattern_match_reduces_score():
    """템플릿 패턴 ('가볍게 OO하면서 OO 나누는') 매치가 점수를 감점시키는지.

    NOTE: ``config/rules/diversity_patterns.yaml`` 은 description 필드의
    인용 처리 오류로 ``yaml.safe_load`` 가 ParserError 를 던져
    ``load_diversity_patterns`` 가 빈 리스트를 반환한다 (silent fail).
    이 문제는 phase3_report.md 에 별도 기록한다.
    여기선 패턴을 인-라인으로 주입해 순수 알고리즘 동작만 검증한다.
    """
    inline_patterns = [
        {
            "id": "gabyeopge_A_B",
            "regex": r"가볍게\s*\S+\s*(?:하면서|하며)\s*\S+\s*나누",
        }
    ]

    template_text = (
        "가볍게 산책하면서 이야기 나누는 모임이에요. "
        "가볍게 산책하면서 마음 나누는 시간."
    )
    plain_text = "수원 야시장 미식 투어와 호수 둘레길 산책 일정"

    a = _StubCandidate(
        variant="A",
        payload={"title": "산책 모임", "summary": template_text},
        meta={"seed_hash": "tmpl1"},
    )
    b = _StubCandidate(
        variant="B",
        payload={
            "title": "야시장 탐방",
            "summary": plain_text,
        },
        meta={"seed_hash": "plain1"},
    )

    scores_no_pattern = compute_diversity(
        [a, b], "feed", approved_cache=(), patterns=[]
    )
    scores_with_pattern = compute_diversity(
        [a, b], "feed", approved_cache=(), patterns=inline_patterns
    )

    # 패턴이 적용되면 a 의 점수만 감소해야 한다 (b 는 매치 안 됨).
    assert scores_with_pattern["tmpl1"] < scores_no_pattern["tmpl1"]
    assert pytest.approx(scores_with_pattern["plain1"], abs=1e-9) == scores_no_pattern["plain1"]


# ---------------------------------------------------------------------------
# extract_text 보조 — 각 content_type 키를 잘 잡는지
# ---------------------------------------------------------------------------


def test_extract_text_per_content_type():
    feed = extract_text({"title": "T", "summary": "S"}, "feed")
    assert "T" in feed and "S" in feed

    detail = extract_text({"title": "T", "description": "D"}, "detail")
    assert "T" in detail and "D" in detail

    plan = extract_text({"steps": [{"activity": "걷기"}, {"activity": "사진"}]}, "plan")
    assert "걷기" in plan and "사진" in plan

    msg = extract_text(
        {
            "recruiting_intro": "안녕하세요",
            "join_approval": "환영합니다",
            "day_of_notice": "오늘 만나요",
            "post_thanks": "고마워요",
        },
        "messages",
    )
    for token in ("안녕하세요", "환영합니다", "오늘", "고마워요"):
        assert token in msg

    review = extract_text({"review_text": "정말 좋았어요"}, "review")
    assert "정말 좋았어요" in review
