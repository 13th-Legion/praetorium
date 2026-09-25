"""Add FTX meal-plan opt-in + payment tracking to event_rsvps.

Attending members must opt in/out of the $15 meal plan (Sat dinner + Sun
breakfast). Adds meal_plan (bool), meal_paid (bool), meal_payment_method
(paypal/venmo/cash), and meal_paid_at.

Revision ID: 0012_event_rsvp_meal_plan
Revises: 0011_seed_ribbon_catalog
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_event_rsvp_meal_plan"
down_revision: Union[str, None] = "0011_seed_ribbon_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "event_rsvps",
        sa.Column("meal_plan", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "event_rsvps",
        sa.Column("meal_paid", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "event_rsvps",
        sa.Column("meal_payment_method", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "event_rsvps",
        sa.Column("meal_paid_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("event_rsvps", "meal_paid_at")
    op.drop_column("event_rsvps", "meal_payment_method")
    op.drop_column("event_rsvps", "meal_paid")
    op.drop_column("event_rsvps", "meal_plan")
