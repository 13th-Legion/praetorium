"""Add rank_history table for promotion/demotion tracking.

Revision ID: a1b2c3d4e5f6
Revises: 06f355945bf5
Create Date: 2026-03-13 13:33:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "a1b2c3d4e5f6"
down_revision = "06f355945bf5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rank_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False, index=True),
        sa.Column("old_rank", sa.String(4), nullable=True),
        sa.Column("new_rank", sa.String(4), nullable=False),
        sa.Column("changed_by", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("effective_date", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("rank_history")
