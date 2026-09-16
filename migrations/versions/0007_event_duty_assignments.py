"""Event-scoped FTX duty team (KP / latrine) — PP-323.

Whiteboard extra duties (KP, latrine, plus any labeled duty) become an
event-scoped assignment table. This does not change Member.team. One
assignment per member per event.

Revision ID: 0007_event_duty_assignments
Revises: 0006_guard_duty_guest_unique
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_event_duty_assignments"
down_revision: Union[str, None] = "0006_guard_duty_guest_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_duty_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("duty_label", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="ad_hoc"),
        sa.Column("geo_team_name", sa.String(length=32), nullable=True),
        sa.Column("assigned_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "member_id", name="uq_duty_assign_member"),
    )
    op.create_index("ix_event_duty_assignments_event_id", "event_duty_assignments", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_event_duty_assignments_event_id", table_name="event_duty_assignments")
    op.drop_table("event_duty_assignments")
