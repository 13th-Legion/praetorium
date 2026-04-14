"""PP-125: After Action Review (AAR) system

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-04-13
"""
from alembic import op
import sqlalchemy as sa

revision = "k2l3m4n5o6p7"
down_revision = "j1k2l3m4n5o6"
branch_labels = None
depends_on = None


def upgrade():
    # AAR fields on events table
    op.add_column("events", sa.Column("aar_commander_intent", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("aar_mission_summary", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("aar_published_at", sa.DateTime(), nullable=True))
    op.add_column("events", sa.Column("aar_published_by", sa.String(64), nullable=True))

    # AAR items table (right/wrong/improve)
    op.create_table(
        "event_aar_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),  # right, wrong, improve
        sa.Column("ordinal", sa.Integer(), default=0),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_aar_items_event", "event_aar_items", ["event_id"])


def downgrade():
    op.drop_index("ix_aar_items_event")
    op.drop_table("event_aar_items")
    op.drop_column("events", "aar_published_by")
    op.drop_column("events", "aar_published_at")
    op.drop_column("events", "aar_mission_summary")
    op.drop_column("events", "aar_commander_intent")
