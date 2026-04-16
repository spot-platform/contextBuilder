"""add latitude/longitude to feed & detail for MVP map pins.

Revision ID: 0002_add_feed_detail_lat_lng
Revises: 0001_initial_schema
Create Date: 2026-04-15
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_add_feed_detail_lat_lng"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "synthetic_feed_content",
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
    )
    op.add_column(
        "synthetic_feed_content",
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
    )
    op.add_column(
        "synthetic_spot_detail",
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
    )
    op.add_column(
        "synthetic_spot_detail",
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("synthetic_spot_detail", "longitude")
    op.drop_column("synthetic_spot_detail", "latitude")
    op.drop_column("synthetic_feed_content", "longitude")
    op.drop_column("synthetic_feed_content", "latitude")
