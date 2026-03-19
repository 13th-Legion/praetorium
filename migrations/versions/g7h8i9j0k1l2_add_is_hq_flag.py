"""Add is_hq boolean flag to members table.

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-19 10:55:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "g7h8i9j0k1l2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("members", sa.Column("is_hq", sa.Boolean(), nullable=True, server_default="false"))


def downgrade() -> None:
    op.drop_column("members", "is_hq")
