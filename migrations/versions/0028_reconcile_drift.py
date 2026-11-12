"""Reconcile model/DB drift: timestamp NOT NULL, meal flags nullable, unit_name unique.

Revision ID: 0028_reconcile_drift
Revises: 0027_s6_crypto
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0028_reconcile_drift"
down_revision: Union[str, None] = "0027_s6_crypto"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfill any NULL timestamps before tightening to NOT NULL.
    op.execute(
        "UPDATE company_routing SET created_at = now(), updated_at = now() "
        "WHERE created_at IS NULL OR updated_at IS NULL"
    )

    # Timestamps: models declare NOT NULL, migrations had created them nullable.
    op.alter_column("company_routing", "created_at", existing_type=sa.DateTime(), nullable=False)
    op.alter_column("company_routing", "updated_at", existing_type=sa.DateTime(), nullable=False)
    op.alter_column("ribbon_citations", "awarded_at", existing_type=sa.DateTime(), nullable=False)
    op.alter_column("ue2_exercises", "created_at", existing_type=sa.DateTime(), nullable=False)
    op.alter_column("ue2_exercises", "updated_at", existing_type=sa.DateTime(), nullable=False)
    op.alter_column("ue2_participants", "created_at", existing_type=sa.DateTime(), nullable=False)
    op.alter_column("ue2_participants", "updated_at", existing_type=sa.DateTime(), nullable=False)
    op.alter_column("ue2_score_events", "created_at", existing_type=sa.DateTime(), nullable=False)

    # Meal flags: models declare nullable (Boolean default False), migrations had NOT NULL.
    op.alter_column("s4_meal_plans", "thu_dinner", existing_type=sa.Boolean(), nullable=True)
    op.alter_column("s4_meal_plans", "fri_breakfast", existing_type=sa.Boolean(), nullable=True)
    op.alter_column("s4_meal_plans", "fri_lunch", existing_type=sa.Boolean(), nullable=True)
    op.alter_column("s4_meal_plans", "fri_dinner", existing_type=sa.Boolean(), nullable=True)

    # company_routing.unit_name: model declares unique=True; migration had a plain index.
    op.drop_index("ix_company_routing_unit_name", table_name="company_routing")
    op.create_unique_constraint("uq_company_routing_unit_name", "company_routing", ["unit_name"])


def downgrade() -> None:
    op.drop_constraint("uq_company_routing_unit_name", "company_routing", type_="unique")
    op.create_index("ix_company_routing_unit_name", "company_routing", ["unit_name"], unique=False)

    op.alter_column("s4_meal_plans", "fri_dinner", existing_type=sa.Boolean(), nullable=False)
    op.alter_column("s4_meal_plans", "fri_lunch", existing_type=sa.Boolean(), nullable=False)
    op.alter_column("s4_meal_plans", "fri_breakfast", existing_type=sa.Boolean(), nullable=False)
    op.alter_column("s4_meal_plans", "thu_dinner", existing_type=sa.Boolean(), nullable=False)

    op.alter_column("ue2_score_events", "created_at", existing_type=sa.DateTime(), nullable=True)
    op.alter_column("ue2_participants", "updated_at", existing_type=sa.DateTime(), nullable=True)
    op.alter_column("ue2_participants", "created_at", existing_type=sa.DateTime(), nullable=True)
    op.alter_column("ue2_exercises", "updated_at", existing_type=sa.DateTime(), nullable=True)
    op.alter_column("ue2_exercises", "created_at", existing_type=sa.DateTime(), nullable=True)
    op.alter_column("ribbon_citations", "awarded_at", existing_type=sa.DateTime(), nullable=True)
    op.alter_column("company_routing", "updated_at", existing_type=sa.DateTime(), nullable=True)
    op.alter_column("company_routing", "created_at", existing_type=sa.DateTime(), nullable=True)
