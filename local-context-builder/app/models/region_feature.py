"""``region_feature`` — per-region computed feature vector.

Plan section 4-5. One row per (region_id, dataset_version). Processor
writes this after STEP 4 (Kakao-only) and updates it during STEP 6
(real-data blended). ``feature_json`` is a forward-compat escape hatch;
prefer dedicated columns when a feature stabilizes.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RegionFeature(Base):
    __tablename__ = "region_feature"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("region_master.id"), nullable=False
    )
    dataset_version: Mapped[str] = mapped_column(String(50), nullable=False)

    # Density (count / area_km2).
    food_density: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    cafe_density: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    activity_density: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    nightlife_density: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    lesson_density: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )

    # Access / suitability scores, normalized 0..1.
    park_access_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    culture_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    night_liveliness_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )

    # Spot-type suitability scores, normalized 0..1.
    casual_meetup_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    lesson_spot_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    solo_activity_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )
    group_activity_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=text("0")
    )

    # Correction / blending fields.
    kakao_raw_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    real_data_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    blended_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_used: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Meta.
    raw_place_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0")
    )
    normalized_place_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0")
    )
    feature_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "region_id", "dataset_version", name="uq_region_feature_region_version"
        ),
    )
