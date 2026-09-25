"""Add per-FTX meal-planning toggle to events.

Adds meal_planning_enabled (bool, default true) so S4 can switch meal planning
off for FTX/MCFTX events that have no meal plan (e.g. a one-day urban evasion).

Revision ID: 0013_event_meal_planning_toggle
Revises: 0012_event_rsvp_meal_plan
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_event_meal_planning_toggle"
down_revision: Union[str, None] = "0012_event_rsvp_meal_plan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("meal_planning_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("events", "meal_planning_enabled")
