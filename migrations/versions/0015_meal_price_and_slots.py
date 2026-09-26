"""Standard-or-override meal price, and Thu/Fri slots for long FTXs.

Revision ID: 0015_meal_price_and_slots
Revises: 0014_audit_fixes
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_meal_price_and_slots"
down_revision: Union[str, None] = "0014_audit_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for name in ("thu_dinner", "fri_breakfast", "fri_lunch", "fri_dinner"):
        op.add_column(
            "s4_meal_plans",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    op.add_column(
        "events",
        sa.Column("meal_price_override", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "meal_price_override")
    for name in ("fri_dinner", "fri_lunch", "fri_breakfast", "thu_dinner"):
        op.drop_column("s4_meal_plans", name)
