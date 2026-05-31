"""Add on-leave fields to members (PP: 6-month leave of absence)

Revision ID: n5o6p7q8r9s0
Revises: m4n5o6p7q8r9
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa

revision = "n5o6p7q8r9s0"
down_revision = "m4n5o6p7q8r9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("members", sa.Column("on_leave", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("members", sa.Column("leave_start", sa.Date(), nullable=True))
    op.add_column("members", sa.Column("leave_end", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("members", "leave_end")
    op.drop_column("members", "leave_start")
    op.drop_column("members", "on_leave")
