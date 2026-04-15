"""SQLAlchemy 2.0 ORM 모델 — synthetic_content_pipeline_plan.md §8 6테이블.

모든 테이블은 dataset_version + spot_id 인덱스를 가진다 (해당 컬럼이 있는 경우).
JSONB는 SQLite 호환을 위해 sqlalchemy.JSON 으로 매핑한다.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pipeline.db.base import Base


def _uuid_str() -> str:
    return str(uuid.uuid4())


class SyntheticFeedContent(Base):
    """Feed preview (리스트 카드)."""

    __tablename__ = "synthetic_feed_content"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    dataset_version: Mapped[str] = mapped_column(String(20), nullable=False)
    spot_id: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    cover_tags_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    supporter_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    price_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    region_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    time_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    validation_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_feed_dataset_spot", "dataset_version", "spot_id"),
        Index("ix_feed_spot", "spot_id"),
    )


class SyntheticSpotDetail(Base):
    """Spot 상세 페이지."""

    __tablename__ = "synthetic_spot_detail"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    dataset_version: Mapped[str] = mapped_column(String(20), nullable=False)
    spot_id: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    plan_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    materials_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    target_audience: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    cost_breakdown_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    host_intro: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    policy_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    validation_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_detail_dataset_spot", "dataset_version", "spot_id"),
        Index("ix_detail_spot", "spot_id"),
    )


class SyntheticSpotMessages(Base):
    """커뮤니케이션 snippet 4종."""

    __tablename__ = "synthetic_spot_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    dataset_version: Mapped[str] = mapped_column(String(20), nullable=False)
    spot_id: Mapped[str] = mapped_column(String(50), nullable=False)
    message_type: Mapped[str] = mapped_column(String(30), nullable=False)
    speaker_type: Mapped[str] = mapped_column(String(20), nullable=False)
    speaker_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_simulated: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    validation_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    __table_args__ = (
        Index("ix_messages_dataset_spot", "dataset_version", "spot_id"),
        Index("ix_messages_spot", "spot_id"),
    )


class SyntheticReview(Base):
    """활동 종료 후 리뷰."""

    __tablename__ = "synthetic_review"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    dataset_version: Mapped[str] = mapped_column(String(20), nullable=False)
    spot_id: Mapped[str] = mapped_column(String(50), nullable=False)
    reviewer_agent_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    rating: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    review_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    sentiment_score: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    validation_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint("rating IS NULL OR (rating BETWEEN 1 AND 5)", name="ck_review_rating_range"),
        Index("ix_review_dataset_spot", "dataset_version", "spot_id"),
        Index("ix_review_spot", "spot_id"),
    )


class ContentValidationLog(Base):
    """모든 검증기(individual / cross / critic / diversity)의 결과 로그."""

    __tablename__ = "content_validation_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    content_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content_id: Mapped[str] = mapped_column(String(36), nullable=False)
    validator_type: Mapped[str] = mapped_column(String(30), nullable=False)
    score: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    reason_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_validation_content", "content_type", "content_id"),
        Index("ix_validation_validator", "validator_type"),
    )


class ContentVersionPolicy(Base):
    """버전 정책 — Plan §9 전환 트리거 상태 머신."""

    __tablename__ = "content_version_policy"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    dataset_version: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # draft/active/deprecated/archived
    activation_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deprecation_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    replacement_version: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    transition_strategy: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    real_content_threshold: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_policy_version", "dataset_version"),
        Index("ix_policy_status", "status"),
    )


__all__ = [
    "SyntheticFeedContent",
    "SyntheticSpotDetail",
    "SyntheticSpotMessages",
    "SyntheticReview",
    "ContentValidationLog",
    "ContentVersionPolicy",
]
