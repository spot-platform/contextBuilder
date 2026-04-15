"""initial schema — 6테이블 생성.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-04-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "synthetic_feed_content",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_version", sa.String(length=20), nullable=False),
        sa.Column("spot_id", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("cover_tags_json", sa.JSON(), nullable=True),
        sa.Column("supporter_label", sa.String(length=50), nullable=True),
        sa.Column("price_label", sa.String(length=50), nullable=True),
        sa.Column("region_label", sa.String(length=50), nullable=True),
        sa.Column("time_label", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("quality_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("validation_status", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_feed_dataset_spot", "synthetic_feed_content", ["dataset_version", "spot_id"])
    op.create_index("ix_feed_spot", "synthetic_feed_content", ["spot_id"])

    op.create_table(
        "synthetic_spot_detail",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_version", sa.String(length=20), nullable=False),
        sa.Column("spot_id", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("materials_json", sa.JSON(), nullable=True),
        sa.Column("target_audience", sa.String(length=100), nullable=True),
        sa.Column("cost_breakdown_json", sa.JSON(), nullable=True),
        sa.Column("host_intro", sa.Text(), nullable=True),
        sa.Column("policy_notes", sa.Text(), nullable=True),
        sa.Column("quality_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("validation_status", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_detail_dataset_spot", "synthetic_spot_detail", ["dataset_version", "spot_id"])
    op.create_index("ix_detail_spot", "synthetic_spot_detail", ["spot_id"])

    op.create_table(
        "synthetic_spot_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_version", sa.String(length=20), nullable=False),
        sa.Column("spot_id", sa.String(length=50), nullable=False),
        sa.Column("message_type", sa.String(length=30), nullable=False),
        sa.Column("speaker_type", sa.String(length=20), nullable=False),
        sa.Column("speaker_id", sa.String(length=50), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at_simulated", sa.DateTime(), nullable=True),
        sa.Column("quality_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("validation_status", sa.String(length=20), nullable=True),
    )
    op.create_index("ix_messages_dataset_spot", "synthetic_spot_messages", ["dataset_version", "spot_id"])
    op.create_index("ix_messages_spot", "synthetic_spot_messages", ["spot_id"])

    op.create_table(
        "synthetic_review",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_version", sa.String(length=20), nullable=False),
        sa.Column("spot_id", sa.String(length=50), nullable=False),
        sa.Column("reviewer_agent_id", sa.String(length=50), nullable=True),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column("review_text", sa.Text(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("sentiment_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("quality_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("validation_status", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("rating IS NULL OR (rating BETWEEN 1 AND 5)", name="ck_review_rating_range"),
    )
    op.create_index("ix_review_dataset_spot", "synthetic_review", ["dataset_version", "spot_id"])
    op.create_index("ix_review_spot", "synthetic_review", ["spot_id"])

    op.create_table(
        "content_validation_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("content_type", sa.String(length=30), nullable=False),
        sa.Column("content_id", sa.String(length=36), nullable=False),
        sa.Column("validator_type", sa.String(length=30), nullable=False),
        sa.Column("score", sa.Numeric(4, 3), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("reason_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_validation_content", "content_validation_log", ["content_type", "content_id"])
    op.create_index("ix_validation_validator", "content_validation_log", ["validator_type"])

    op.create_table(
        "content_version_policy",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("activation_date", sa.DateTime(), nullable=True),
        sa.Column("deprecation_date", sa.DateTime(), nullable=True),
        sa.Column("replacement_version", sa.String(length=20), nullable=True),
        sa.Column("transition_strategy", sa.String(length=20), nullable=True),
        sa.Column("real_content_threshold", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_policy_version", "content_version_policy", ["dataset_version"])
    op.create_index("ix_policy_status", "content_version_policy", ["status"])


def downgrade() -> None:
    op.drop_index("ix_policy_status", table_name="content_version_policy")
    op.drop_index("ix_policy_version", table_name="content_version_policy")
    op.drop_table("content_version_policy")

    op.drop_index("ix_validation_validator", table_name="content_validation_log")
    op.drop_index("ix_validation_content", table_name="content_validation_log")
    op.drop_table("content_validation_log")

    op.drop_index("ix_review_spot", table_name="synthetic_review")
    op.drop_index("ix_review_dataset_spot", table_name="synthetic_review")
    op.drop_table("synthetic_review")

    op.drop_index("ix_messages_spot", table_name="synthetic_spot_messages")
    op.drop_index("ix_messages_dataset_spot", table_name="synthetic_spot_messages")
    op.drop_table("synthetic_spot_messages")

    op.drop_index("ix_detail_spot", table_name="synthetic_spot_detail")
    op.drop_index("ix_detail_dataset_spot", table_name="synthetic_spot_detail")
    op.drop_table("synthetic_spot_detail")

    op.drop_index("ix_feed_spot", table_name="synthetic_feed_content")
    op.drop_index("ix_feed_dataset_spot", table_name="synthetic_feed_content")
    op.drop_table("synthetic_feed_content")
