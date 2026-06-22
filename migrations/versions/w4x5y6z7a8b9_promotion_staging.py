"""Promotion staging — promotion_stages table for staged FTX/formation rank changes

Revision ID: w4x5y6z7a8b9
Revises: v3w4x5y6z7a8
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa


revision = "w4x5y6z7a8b9"
down_revision = "v3w4x5y6z7a8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "promotion_stages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("from_rank", sa.String(length=4), nullable=True),
        sa.Column("to_rank", sa.String(length=4), nullable=False),
        sa.Column("action_type", sa.String(length=16), nullable=False, server_default="promotion"),
        sa.Column("is_officer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="staged"),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("staged_by", sa.String(length=64), nullable=True),
        sa.Column("staged_at", sa.DateTime(), nullable=True),
        sa.Column("finalized_by", sa.String(length=64), nullable=True),
        sa.Column("finalized_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_promotion_stages_member_id", "promotion_stages", ["member_id"], unique=False
    )


def downgrade():
    op.drop_index("ix_promotion_stages_member_id", table_name="promotion_stages")
    op.drop_table("promotion_stages")
