"""``category_mapping_rule`` — Kakao → internal tag mapping rules.

Plan section 4-3. Data-driven replacement for hard-coded if/else chains.
Seeded by ``scripts/load_category_mapping.py`` from
``data/category_mapping_seed.json``. Processor consults this table at
normalization time.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CategoryMappingRule(Base):
    __tablename__ = "category_mapping_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kakao_category_group_code: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    kakao_category_pattern: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    keyword_pattern: Mapped[str | None] = mapped_column(String(200), nullable=True)
    internal_tag: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("1.0")
    )
    priority: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0")
    )
    is_active: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("true")
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
