"""``place_raw_kakao`` — untouched Kakao Local API responses.

Plan section 4-2. Collector writes one row per (source_place_id,
region_id). Keep the original JSON in ``raw_json`` so processor can
re-derive fields if the normalization logic changes without re-hitting
the Kakao API.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PlaceRawKakao(Base):
    __tablename__ = "place_raw_kakao"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("region_master.id"), nullable=False
    )
    source_place_id: Mapped[str] = mapped_column(String(30), nullable=False)
    place_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    category_group_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    category_group_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    road_address_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    place_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    distance: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    search_type: Mapped[str] = mapped_column(String(20), nullable=False)
    search_query: Mapped[str | None] = mapped_column(String(100), nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    batch_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "source_place_id", "region_id", name="uq_place_raw_source_region"
        ),
        Index("idx_place_raw_region", "region_id"),
        Index("idx_place_raw_source_id", "source_place_id"),
        Index("idx_place_raw_batch", "batch_id"),
    )
