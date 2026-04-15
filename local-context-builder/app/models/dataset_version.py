"""``dataset_version`` — batch build manifest and status tracker.

Plan section 4-9. Every feature/persona/spot row is tagged with a
``dataset_version`` string; this table is the registry that says which
versions are ``building`` vs ``success`` vs ``failed``. STEP 9 and
STEP 10 both write to it (building → success/failed).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DatasetVersion(Base):
    __tablename__ = "dataset_version"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    build_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_city: Mapped[str | None] = mapped_column(String(20), nullable=True)
    built_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_window_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_window_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    region_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    place_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(
        String(20), nullable=True, server_default=text("'building'")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )

    __table_args__ = (Index("idx_dataset_version_status", "status"),)
