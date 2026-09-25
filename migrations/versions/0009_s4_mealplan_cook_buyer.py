"""S4 meal plan: split cook/buyer into independent fields.

The single `assigned_to_id` (cook/buyer combined) becomes two independent
columns — `cook_id` and `buyer_id` — because the buyer is not always the cook,
and either role can be any member (not just S4).

Revision ID: 0009_s4_mealplan_cook_buyer
Revises: 0008_s2_intel
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_s4_mealplan_cook_buyer"
down_revision: Union[str, None] = "0008_s2_intel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("s4_meal_plans", "assigned_to_id", new_column_name="cook_id")
    op.add_column("s4_meal_plans", sa.Column("buyer_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_s4_meal_plans_buyer_id", "s4_meal_plans", "members", ["buyer_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_s4_meal_plans_buyer_id", "s4_meal_plans", type_="foreignkey")
    op.drop_column("s4_meal_plans", "buyer_id")
    op.alter_column("s4_meal_plans", "cook_id", new_column_name="assigned_to_id")
