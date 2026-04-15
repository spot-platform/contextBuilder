"""``real_activity_agg`` — pre-aggregated real-service activity snapshot.

Plan section 4-6. Built by STEP 5 from the read-only real-service DB.
This table is the only place where real-service metrics live inside the
batch DB, so processor can compute ``blended_score`` without
reconnecting to the production read replica every run.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RealActivityAgg(Base):
    __tablename__ = "real_activity_agg"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("region_master.id"), nullable=False
    )
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Base counts.
    real_spot_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0")
    )
    real_join_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0")
    )
    real_completion_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0")
    )
    real_cancel_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0")
    )
    real_noshow_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0")
    )

    # Ratios.
    completion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    cancel_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    noshow_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Category share.
    real_food_spot_ratio: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    real_activity_spot_ratio: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    real_lesson_spot_ratio: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    real_night_spot_ratio: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )

    # Time-slot distribution (flexible JSON).
    time_slot_distribution: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    real_avg_group_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    real_hot_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "region_id",
            "window_start",
            "window_end",
            name="uq_real_activity_region_window",
        ),
    )
