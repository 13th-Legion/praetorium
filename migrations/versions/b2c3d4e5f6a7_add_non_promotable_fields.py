"""Add non_promotable_until and non_promotable_reason fields.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-13 13:57:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("non_promotable_until", sa.Date(), nullable=True))
    op.add_column("members", sa.Column("non_promotable_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("members", "non_promotable_reason")
    op.drop_column("members", "non_promotable_until")
