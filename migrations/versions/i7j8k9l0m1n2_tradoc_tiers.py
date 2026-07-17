"""Add tradoc_tiers table (manageable TRADOC categories) + seed existing two.

Blocks continue to reference a tier by string key (TradocBlock.tier). This table
makes the tier list (label/subtitle/order/archived) fully CRUD-manageable instead
of hardcoded in code.

Revision ID: i7j8k9l0m1n2
Revises: h6i7j8k9l0m1
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa


revision = "i7j8k9l0m1n2"
down_revision = "h6i7j8k9l0m1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tradoc_tiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=96), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("key", name="uq_tradoc_tiers_key"),
    )
    # Seed the two existing hardcoded tiers so nothing changes visually.
    tiers = sa.table(
        "tradoc_tiers",
        sa.column("key", sa.String),
        sa.column("label", sa.String),
        sa.column("subtitle", sa.Text),
        sa.column("sort_order", sa.Integer),
        sa.column("archived", sa.Boolean),
    )
    op.bulk_insert(
        tiers,
        [
            {
                "key": "initial",
                "label": "Initial Entry Training",
                "subtitle": "The patching pipeline — what every Legionary completes to earn the patch.",
                "sort_order": 0,
                "archived": False,
            },
            {
                "key": "advanced",
                "label": "Advanced Qualifications & Tabs",
                "subtitle": "Earned above and beyond patching — statewide courses and the 13th Legion's own tabs.",
                "sort_order": 1,
                "archived": False,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("tradoc_tiers")
