"""Add is_state_s1 flag to company_routing + seed the State S1 copy row.

Revision ID: 0034_company_routing_state_s1
Revises: 0033_volunteer_service_grants
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034_company_routing_state_s1"
down_revision: Union[str, None] = "0033_volunteer_service_grants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company_routing",
        sa.Column("is_state_s1", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # Seed the reserved State S1 row (the state-wide copy address). Idempotent.
    op.execute(
        sa.text(
            "INSERT INTO company_routing (unit_name, route_type, email, is_state_s1, created_at, updated_at) "
            "VALUES ('State S1', 'email', 'admin@texasstatemilitia.org', true, now(), now()) "
            "ON CONFLICT (unit_name) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM company_routing WHERE is_state_s1 = true"))
    op.drop_column("company_routing", "is_state_s1")
