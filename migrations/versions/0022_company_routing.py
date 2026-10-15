"""Create company_routing table for TSM unit application routing.

Revision ID: 0022_company_routing
Revises: 0021_notification_source_id
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022_company_routing"
down_revision: Union[str, None] = "0021_notification_source_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_routing",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_name", sa.String(128), nullable=False),
        sa.Column("route_type", sa.String(16), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_company_routing_unit_name", "company_routing", ["unit_name"], unique=True
    )

    # Seed with the mapping previously hardcoded in the recruit daemon.
    rows = [
        ("13th Legion (DFW)", "deck", None),
        ("Archangels (Corpus Christi)", "email", "jbarnes0526@protonmail.com"),
        ("Guardians (Austin)", "email", "jlacey2@protonmail.com"),
        ("Vikings (Houston)", "email", "admin@tsmhouston.org"),
        ("Centurions (Brazoria)", "email", "glassesM@protonmail.com"),
        ("Brazos (Waco)", "email", "TSMBrazos@proton.me"),
    ]
    cr = sa.table(
        "company_routing",
        sa.column("unit_name", sa.String),
        sa.column("route_type", sa.String),
        sa.column("email", sa.String),
    )
    op.bulk_insert(
        cr,
        [{"unit_name": n, "route_type": t, "email": e} for n, t, e in rows],
    )


def downgrade() -> None:
    op.drop_index("ix_company_routing_unit_name", table_name="company_routing")
    op.drop_table("company_routing")
