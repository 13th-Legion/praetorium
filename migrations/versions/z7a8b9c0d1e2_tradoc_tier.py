"""TRADOC block tier — Initial Entry Training vs Advanced Qualifications & Tabs (PP-232)

Revision ID: z7a8b9c0d1e2
Revises: y6z7a8b9c0d1
Create Date: 2026-06-30
"""
from alembic import op
import sqlalchemy as sa


revision = "z7a8b9c0d1e2"
down_revision = "y6z7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade():
    # Add tier column; existing blocks all belong to the initial (patching) tier.
    op.add_column(
        "tradoc_blocks",
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="initial"),
    )
    # Explicit backfill (defensive — server_default already covers existing rows).
    op.execute("UPDATE tradoc_blocks SET tier = 'initial' WHERE tier IS NULL OR tier = ''")


def downgrade():
    op.drop_column("tradoc_blocks", "tier")
