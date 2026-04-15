"""``place_normalized`` — cleaned, tagged places produced from raw Kakao.

Plan section 4-4. Processor owns writes to this table. Multi-tag: a
place may have several ``is_*`` booleans set simultaneously (e.g. a
restaurant with a bar can be both ``is_food`` and ``is_nightlife``).
``primary_category`` is the dominant tag chosen by priority rules.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PlaceNormalized(Base):
    __tablename__ = "place_normalized"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("region_master.id"), nullable=False
    )
    source: Mapped[str | None] = mapped_column(
        String(20), nullable=True, server_default=text("'kakao'")
    )
    source_place_id: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    primary_category: Mapped[str] = mapped_column(String(30), nullable=False)
    sub_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    address_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    road_address_name: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Multi-tag booleans (plan §4-4 lists 7 base + 2 derived).
    is_food: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    is_cafe: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    is_activity: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    is_park: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    is_culture: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    is_nightlife: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    is_lesson: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    # Derived tags.
    is_night_friendly: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )
    is_group_friendly: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("false")
    )

    mapping_confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("1.0")
    )
    collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("source", "source_place_id", name="uq_place_norm_source"),
        Index("idx_place_norm_region", "region_id"),
        Index("idx_place_norm_category", "primary_category"),
    )
