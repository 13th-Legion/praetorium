"""Add buddy_exclude flag to event_rsvps for battle-buddy auto-pair exclusions.

Revision ID: 0029_buddy_exclude
Revises: 0028_reconcile_drift
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029_buddy_exclude"
down_revision: Union[str, None] = "0028_reconcile_drift"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "event_rsvps",
        sa.Column("buddy_exclude", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("event_rsvps", "buddy_exclude")
