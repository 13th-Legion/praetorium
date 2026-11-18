"""Add volunteer_service_grants table (PP-424).

One row per Volunteer Service auto-award on a finalized volunteering event,
mirroring mission_leader_grants: finalize writes a row per confirmed attendee
and unfinalize revokes exactly those rows so a re-finalize doesn't double-
increment device_count for the same volunteer stint.

Revision ID: 0033_volunteer_service_grants
Revises: 0032_guard_duty_sets
Create Date: 2026-11-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0033_volunteer_service_grants"
down_revision: Union[str, None] = "0032_guard_duty_sets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "volunteer_service_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "member_id", name="uq_volunteer_service_grant"),
    )
    op.create_index("ix_volunteer_service_grants_event_id", "volunteer_service_grants", ["event_id"])
    op.create_index("ix_volunteer_service_grants_member_id", "volunteer_service_grants", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_volunteer_service_grants_member_id", table_name="volunteer_service_grants")
    op.drop_index("ix_volunteer_service_grants_event_id", table_name="volunteer_service_grants")
    op.drop_table("volunteer_service_grants")
