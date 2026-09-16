"""Unique constraint for guests on guard slots (PP-325).

event_guard_duty.guest_id already existed and assign_guard already accepted
guest_id, but the only unique key was (event_id, slot_number, member_id).
NULL member_id on guest rows meant two identical guest assignments to the
same slot were legal. This adds the guest-side twin.

Revision ID: 0006_guard_duty_guest_unique
Revises: 0005_event_rsvp_immunes
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006_guard_duty_guest_unique"
down_revision: Union[str, None] = "0005_event_rsvp_immunes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_guard_event_slot_guest",
        "event_guard_duty",
        ["event_id", "slot_number", "guest_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_guard_event_slot_guest",
        "event_guard_duty",
        type_="unique",
    )
