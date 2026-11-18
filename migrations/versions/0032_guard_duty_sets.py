"""Add multi-set guard duty columns (set_number + set_label).

Revision ID: 0032_guard_duty_sets
Revises: 0031_member_no_media
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0032_guard_duty_sets"
down_revision: Union[str, None] = "0031_member_no_media"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("event_guard_slots", sa.Column("set_number", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.add_column("event_guard_slots", sa.Column("set_label", sa.String(length=64), nullable=True))
    op.add_column("event_guard_duty", sa.Column("set_label", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("event_guard_duty", "set_label")
    op.drop_column("event_guard_slots", "set_label")
    op.drop_column("event_guard_slots", "set_number")
