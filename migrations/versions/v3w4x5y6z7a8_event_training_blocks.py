"""Events multi-block: training_blocks (comma-separated) + backfill from training_block

Revision ID: v3w4x5y6z7a8
Revises: u2v3w4x5y6z7
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa


revision = "v3w4x5y6z7a8"
down_revision = "u2v3w4x5y6z7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("training_blocks", sa.String(length=64), nullable=True))
    # Backfill: copy existing single training_block int into the CSV field
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE events SET training_blocks = CAST(training_block AS VARCHAR) "
        "WHERE training_block IS NOT NULL"
    ))


def downgrade():
    op.drop_column("events", "training_blocks")
