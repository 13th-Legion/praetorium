"""Add event finalization fields.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-16 10:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("finalized_at", sa.DateTime(), nullable=True))
    op.add_column("events", sa.Column("finalized_by", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("events", "finalized_by")
    op.drop_column("events", "finalized_at")
