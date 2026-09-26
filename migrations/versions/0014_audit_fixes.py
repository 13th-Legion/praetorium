"""Mission Leader grant ledger + exact S4 money columns.

Revision ID: 0014_audit_fixes
Revises: 0013_event_meal_planning_toggle
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_audit_fixes"
down_revision: Union[str, None] = "0013_event_meal_planning_toggle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mission_leader_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "member_id", name="uq_mission_leader_grant"),
    )
    op.create_index("ix_mission_leader_grants_event_id", "mission_leader_grants", ["event_id"])
    op.create_index("ix_mission_leader_grants_member_id", "mission_leader_grants", ["member_id"])

    op.alter_column(
        "s4_expenses",
        "amount",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
        postgresql_using="amount::numeric(12,2)",
    )
    op.alter_column(
        "s4_purchase_requests",
        "estimated_cost",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
        postgresql_using="estimated_cost::numeric(12,2)",
    )


def downgrade() -> None:
    op.alter_column(
        "s4_purchase_requests",
        "estimated_cost",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="estimated_cost::double precision",
    )
    op.alter_column(
        "s4_expenses",
        "amount",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="amount::double precision",
    )
    op.drop_index("ix_mission_leader_grants_member_id", table_name="mission_leader_grants")
    op.drop_index("ix_mission_leader_grants_event_id", table_name="mission_leader_grants")
    op.drop_table("mission_leader_grants")
