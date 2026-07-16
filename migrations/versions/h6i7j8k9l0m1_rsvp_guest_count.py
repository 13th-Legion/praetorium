"""Add guest_count to event_rsvps (self-reported headcount, e.g. Family Day).

Revision ID: h6i7j8k9l0m1
Revises: g5h6i7j8k9l0
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa


revision = "h6i7j8k9l0m1"
down_revision = "g5h6i7j8k9l0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_rsvps",
        sa.Column("guest_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("event_rsvps", "guest_count")
