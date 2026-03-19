"""Add event_schedule_blocks table for FTX Builder (PP-070 / S3 Dashboard)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-17 13:40:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_schedule_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("start_time", sa.String(4), nullable=False),
        sa.Column("end_time", sa.String(4), nullable=True),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("activity_type", sa.String(16), nullable=False, server_default="class"),
        sa.Column("instructor_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_schedule_blocks_event_id", "event_schedule_blocks", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_schedule_blocks_event_id", table_name="event_schedule_blocks")
    op.drop_table("event_schedule_blocks")
