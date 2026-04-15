"""``persona_region_weight`` — persona × region affinity matrix.

Plan section 4-7. Produced by STEP 8. One row per (dataset_version,
persona_type, region_id). ``explanation_json`` stores the per-feature
contribution so downstream QA can debug why a given persona ended up
weighted heavily in a given region.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PersonaRegionWeight(Base):
    __tablename__ = "persona_region_weight"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset_version: Mapped[str] = mapped_column(String(50), nullable=False)
    persona_type: Mapped[str] = mapped_column(String(50), nullable=False)
    region_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("region_master.id"), nullable=False
    )
    affinity_score: Mapped[float] = mapped_column(Float, nullable=False)
    create_offer_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    create_request_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    join_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "dataset_version",
            "persona_type",
            "region_id",
            name="uq_persona_region_weight",
        ),
    )
