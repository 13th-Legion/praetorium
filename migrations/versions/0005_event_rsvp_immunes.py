"""Event-scoped Immunes flag on RSVP (PP-324).

Roman *immunes* = soldiers exempt from fatigue duties. Guard auto-assign was
dumping every checked-in member into slots, including medics, 1SG, instructors,
and the injured. This is an EVENT flag on event_rsvps, not a standing
member-profile field.

Revision ID: 0005_event_rsvp_immunes
Revises: 0004_event_fragos
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_event_rsvp_immunes"
down_revision: Union[str, None] = "0004_event_fragos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "event_rsvps",
        sa.Column("immunes", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("event_rsvps", "immunes")
