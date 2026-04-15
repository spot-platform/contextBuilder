"""Unit tests for ``app.processors.normalize_places`` pure helpers.

Process-level testing against a live Postgres DB lives in
``integration-qa``; these tests only exercise the pure helper
functions that do not require a session.
"""

from __future__ import annotations

from app.processors.category_mapper import map_place
from app.processors.normalize_places import _derived_tags


class _Rule:
    def __init__(
        self,
        internal_tag,
        kakao_category_group_code=None,
        kakao_category_pattern=None,
        keyword_pattern=None,
        priority=0,
        confidence=1.0,
    ):
        self.internal_tag = internal_tag
        self.kakao_category_group_code = kakao_category_group_code
        self.kakao_category_pattern = kakao_category_pattern
        self.keyword_pattern = keyword_pattern
        self.priority = priority
        self.confidence = confidence
        self.is_active = True


class _Raw:
    def __init__(self, **kw):
        self.category_name = kw.get("category_name")
        self.category_group_code = kw.get("category_group_code")
        self.search_query = kw.get("search_query")
        self.place_name = kw.get("place_name", "Sample")
        self.x = kw.get("x", 0.0)
        self.y = kw.get("y", 0.0)


def test_derived_tag_night_friendly_from_nightlife_tag():
    night, _ = _derived_tags("nightlife", {"is_nightlife": True}, "bar")
    assert night is True


def test_derived_tag_night_friendly_from_category_name():
    night, _ = _derived_tags("food", {}, "음식점 > 주점")
    assert night is True


def test_derived_tag_group_friendly_from_lesson_tag():
    _, group = _derived_tags("lesson", {"is_lesson": True}, "원데이클래스")
    assert group is True


def test_derived_tag_group_friendly_from_category_name():
    _, group = _derived_tags("food", {}, "파티룸 전문")
    assert group is True


def test_derived_tag_both_false_default():
    night, group = _derived_tags("food", {}, "음식점 > 한식")
    assert night is False
    assert group is False


def test_mapper_produces_tag_dict_compatible_with_normalizer():
    """map_place must return the tag dict shape normalize_places expects
    (``is_<tag>`` keys that line up with place_normalized columns)."""

    rules = [
        _Rule(internal_tag="food", kakao_category_group_code="FD6", priority=10),
        _Rule(
            internal_tag="nightlife",
            kakao_category_pattern="%주점%",
            priority=9,
            confidence=0.95,
        ),
    ]
    raw = _Raw(category_group_code="FD6", category_name="음식점 > 주점")
    primary, tags, confidence = map_place(raw, rules)

    assert primary == "nightlife"  # higher primary priority
    assert tags.get("is_food") is True
    assert tags.get("is_nightlife") is True
    # confidence is max of matched (food=1.0, nightlife=0.95)
    assert confidence == 1.0

    night, group = _derived_tags(primary, tags, raw.category_name)
    assert night is True
    assert group is False
