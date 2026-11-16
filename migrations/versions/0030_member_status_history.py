"""Add member_status_history audit table.

Revision ID: 0030_member_status_history
Revises: 0029_buddy_exclude
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_member_status_history"
down_revision: Union[str, None] = "0029_buddy_exclude"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "member_status_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("old_status", sa.String(16), nullable=True),
        sa.Column("new_status", sa.String(16), nullable=False),
        sa.Column("changed_by", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_member_status_history_member_id", "member_status_history", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_member_status_history_member_id", table_name="member_status_history")
    op.drop_table("member_status_history")
