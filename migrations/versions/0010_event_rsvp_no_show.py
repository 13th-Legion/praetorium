"""Event RSVP: first-class no-show flag.

Adds `no_show` to event_rsvps — RSVP'd attending but did not attend, marked
explicitly post-event by S1/Command. Distinct from `attended=False` (which also
means "attendance not yet confirmed"), so the ops roster and attendance
confirmation can show a definitive no-show instead of inferring it.

Revision ID: 0010_event_rsvp_no_show
Revises: 0009_s4_mealplan_cook_buyer
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_event_rsvp_no_show"
down_revision: Union[str, None] = "0009_s4_mealplan_cook_buyer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "event_rsvps",
        sa.Column("no_show", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("event_rsvps", "no_show")
