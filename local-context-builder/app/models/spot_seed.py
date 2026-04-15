"""``spot_seed_dataset`` — final published spot seed rows.

Plan section 4-8. STEP 9 writes these as the publish artifact. The
``payload_json`` column carries any extra fields that downstream
services (matching, recommendation) want to ship along with each seed
without growing the schema.
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SpotSeedDataset(Base):
    __tablename__ = "spot_seed_dataset"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset_version: Mapped[str] = mapped_column(String(50), nullable=False)
    region_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("region_master.id"), nullable=False
    )
    spot_type: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    expected_demand_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_supply_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommended_time_slots: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    price_band: Mapped[str | None] = mapped_column(String(20), nullable=True)
    final_weight: Mapped[float] = mapped_column(Float, nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "dataset_version",
            "region_id",
            "spot_type",
            "category",
            name="uq_spot_seed_version_region_type_category",
        ),
    )
