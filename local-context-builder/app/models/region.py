"""``region_master`` — administrative region (행정동) master table.

Plan section 4-1. Every other data table eventually references this via
``region_id``. Suwon rows are the only ones flagged ``is_active=True``
and ``target_city='suwon'`` at v1.0; nationwide expansion happens by
flipping those two columns, not by adding tables.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RegionMaster(Base):
    __tablename__ = "region_master"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    region_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    sido: Mapped[str] = mapped_column(String(20), nullable=False)
    sigungu: Mapped[str] = mapped_column(String(20), nullable=False)
    emd: Mapped[str] = mapped_column(String(30), nullable=False)
    center_lng: Mapped[float] = mapped_column(Float, nullable=False)
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_min_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_min_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_max_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_max_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_km2: Mapped[float | None] = mapped_column(Float, nullable=True)
    grid_level: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True, server_default=text("0")
    )
    target_city: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, server_default=text("true")
    )
    last_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_region_target_city", "target_city"),
        Index("idx_region_active", "is_active"),
        Index("idx_region_last_collected", "last_collected_at"),
    )
