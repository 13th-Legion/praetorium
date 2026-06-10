"""Recurring events: series_id, recurrence_rule, is_series_master on events

Revision ID: u2v3w4x5y6z7
Revises: t1u2v3w4x5y6
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa


revision = "u2v3w4x5y6z7"
down_revision = "t1u2v3w4x5y6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("series_id", sa.String(length=36), nullable=True))
    op.add_column("events", sa.Column("recurrence_rule", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("is_series_master", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_events_series_id", "events", ["series_id"])


def downgrade():
    op.drop_index("ix_events_series_id", table_name="events")
    op.drop_column("events", "is_series_master")
    op.drop_column("events", "recurrence_rule")
    op.drop_column("events", "series_id")
