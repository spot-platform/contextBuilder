"""Unit tests for ``app.processors.category_mapper``.

These tests do not touch the database: fake rule/raw objects emulate
the SQLAlchemy model surface so the pure matching logic can be
exercised quickly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.processors.category_mapper import _like, map_place


@dataclass
class FakeRule:
    internal_tag: str
    kakao_category_group_code: Optional[str] = None
    kakao_category_pattern: Optional[str] = None
    keyword_pattern: Optional[str] = None
    confidence: float = 1.0
    priority: int = 0
    is_active: bool = True


@dataclass
class FakeRaw:
    place_name: str = "Sample"
    category_name: Optional[str] = None
    category_group_code: Optional[str] = None
    search_query: Optional[str] = None
    x: float = 0.0
    y: float = 0.0


def test_like_patterns():
    assert _like("음식점 > 술집", "%주점%") is False
    assert _like("음식점 > 주점", "%주점%") is True
    assert _like("맛집탐방", "맛집%") is True
    assert _like("맛집탐방", "%탐방") is True
    assert _like(None, "%x%") is False
    assert _like("abc", None) is False


def test_map_place_group_code_exact_match():
    rules = [FakeRule(internal_tag="food", kakao_category_group_code="FD6", priority=10)]
    raw = FakeRaw(category_group_code="FD6", category_name="음식점 > 한식")
    primary, tags, confidence = map_place(raw, rules)
    assert primary == "food"
    assert tags == {"is_food": True}
    assert confidence == 1.0


def test_map_place_multi_tag_cafe_and_lesson():
    rules = [
        FakeRule(
            internal_tag="cafe",
            kakao_category_group_code="CE7",
            priority=10,
        ),
        FakeRule(
            internal_tag="lesson",
            kakao_category_pattern="%공방%",
            priority=8,
            confidence=0.9,
        ),
    ]
    raw = FakeRaw(
        category_group_code="CE7",
        category_name="카페 > 공방 카페",
    )
    primary, tags, confidence = map_place(raw, rules)
    # lesson wins primary by _PRIMARY_PRIORITY order (lesson > cafe).
    assert primary == "lesson"
    assert tags == {"is_cafe": True, "is_lesson": True}
    assert confidence == 1.0  # max of matched confidences


def test_map_place_nightlife_keyword_pattern():
    rules = [
        FakeRule(
            internal_tag="nightlife",
            kakao_category_pattern="%주점%",
            priority=9,
            confidence=0.95,
        ),
    ]
    raw = FakeRaw(category_name="음식점 > 주점 > 포차")
    primary, tags, confidence = map_place(raw, rules)
    assert primary == "nightlife"
    assert tags == {"is_nightlife": True}
    assert confidence == 0.95


def test_map_place_unmapped_returns_other():
    rules = [FakeRule(internal_tag="food", kakao_category_group_code="FD6")]
    raw = FakeRaw(category_group_code="ZZ9", category_name="미분류")
    primary, tags, confidence = map_place(raw, rules)
    assert primary == "other"
    assert tags == {}
    assert confidence == 0.0


def test_map_place_keyword_search_match():
    rules = [
        FakeRule(
            internal_tag="lesson",
            keyword_pattern="%원데이클래스%",
            priority=7,
            confidence=0.85,
        ),
    ]
    raw = FakeRaw(
        category_group_code=None,
        category_name=None,
        search_query="영통동 원데이클래스",
    )
    primary, tags, confidence = map_place(raw, rules)
    assert primary == "lesson"
    assert tags == {"is_lesson": True}
    assert confidence == 0.85
