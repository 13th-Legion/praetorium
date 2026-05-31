"""Add optional flag to tradoc_items (advanced/expert land nav are extra)

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa

revision = "o6p7q8r9s0t1"
down_revision = "n5o6p7q8r9s0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tradoc_items", sa.Column("optional", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("tradoc_items", "optional")
