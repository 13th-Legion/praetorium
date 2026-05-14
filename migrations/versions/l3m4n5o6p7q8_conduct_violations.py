"""Conduct violation history table

Revision ID: l3m4n5o6p7q8
Revises: k2l3m4n5o6p7
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa

revision = "l3m4n5o6p7q8"
down_revision = "e41bcdde1415"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conduct_violations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("violation_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("action_taken", sa.String(64), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("issued_by", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_conduct_violations_member", "conduct_violations", ["member_id"])


def downgrade():
    op.drop_index("ix_conduct_violations_member")
    op.drop_table("conduct_violations")
