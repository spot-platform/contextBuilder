"""Kakao category → internal tag mapping (plan §6 STEP 3).

All mapping rules live in the ``category_mapping_rule`` database table;
this module **must not** hard-code Kakao category codes or keywords.
The public API is:

- :func:`load_rules` — fetch active rules, ordered by priority DESC.
- :func:`map_place` — given a raw Kakao place and the rule list, return
  ``(primary_category, tag_dict, mapping_confidence)``.

``tag_dict`` keys match the bool columns on ``place_normalized``
(``is_food``, ``is_cafe``, ``is_activity``, ``is_park``, ``is_culture``,
``is_nightlife``, ``is_lesson``). A single raw row may legitimately
set multiple tags simultaneously (e.g. "카페 겸 공방" → ``is_cafe`` +
``is_lesson``).
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select

from app.models.category_mapping_rule import CategoryMappingRule
from app.models.place_raw import PlaceRawKakao

logger = logging.getLogger(__name__)

# Internal tags that correspond 1:1 to ``place_normalized.is_*`` columns.
INTERNAL_TAGS: tuple[str, ...] = (
    "food",
    "cafe",
    "activity",
    "park",
    "culture",
    "nightlife",
    "lesson",
)

# Primary category selection order when multiple tags match. Higher
# priority wins the ``primary_category`` slot even when several ``is_*``
# booleans are set.
_PRIMARY_PRIORITY: tuple[str, ...] = (
    "nightlife",
    "lesson",
    "culture",
    "activity",
    "park",
    "food",
    "cafe",
)


def load_rules(db) -> list[CategoryMappingRule]:
    """Return all active mapping rules, priority DESC."""

    stmt = (
        select(CategoryMappingRule)
        .where(CategoryMappingRule.is_active.is_(True))
        .order_by(CategoryMappingRule.priority.desc())
    )
    return list(db.scalars(stmt).all())


def _like(value: str | None, pattern: str | None) -> bool:
    """Cheap SQL-LIKE-style match used by the rule engine.

    Supports ``%token%``, ``%suffix``, ``prefix%`` and plain ``literal``.
    Case-insensitive to tolerate Korean/English casing in Kakao data.
    """

    if value is None or pattern is None:
        return False
    v = value.lower()
    p = pattern.lower()
    starts = p.startswith("%")
    ends = p.endswith("%")
    core = p.strip("%")
    if not core:
        return False
    if starts and ends:
        return core in v
    if starts:
        return v.endswith(core)
    if ends:
        return v.startswith(core)
    return core == v


def _rule_matches(rule: CategoryMappingRule, raw: PlaceRawKakao) -> bool:
    """Priority order inside one rule row: group_code → category_pattern → keyword_pattern."""

    if rule.kakao_category_group_code:
        if rule.kakao_category_group_code == (raw.category_group_code or ""):
            return True
        # fall through; other predicates may still match
    if rule.kakao_category_pattern and _like(
        raw.category_name, rule.kakao_category_pattern
    ):
        return True
    if rule.keyword_pattern and _like(raw.search_query, rule.keyword_pattern):
        return True
    return False


def _primary_from_tags(tags: dict[str, bool]) -> str | None:
    for name in _PRIMARY_PRIORITY:
        if tags.get(f"is_{name}"):
            return name
    return None


def map_place(
    raw: PlaceRawKakao, rules: Iterable[CategoryMappingRule]
) -> tuple[str, dict[str, bool], float]:
    """Classify a single raw Kakao row.

    Returns ``(primary_category, tag_dict, mapping_confidence)``.

    - ``tag_dict`` only contains keys whose value is True; absent keys
      mean the tag is ``False`` / default.
    - ``mapping_confidence`` is the max of the matched rules'
      ``confidence`` values, or ``0.0`` when the row is unmapped.
    - Unmapped rows return ``("other", {}, 0.0)``.
    """

    tags: dict[str, bool] = {}
    matched_confidences: list[float] = []

    # Rules are already ordered priority DESC when loaded, so the first
    # match for each internal_tag wins.
    for rule in rules:
        if rule.internal_tag not in INTERNAL_TAGS:
            continue
        if _rule_matches(rule, raw):
            tag_key = f"is_{rule.internal_tag}"
            if tag_key not in tags:
                tags[tag_key] = True
                matched_confidences.append(float(rule.confidence or 1.0))

    primary = _primary_from_tags(tags)
    if primary is None:
        return "other", {}, 0.0
    confidence = max(matched_confidences) if matched_confidences else 1.0
    return primary, tags, confidence
