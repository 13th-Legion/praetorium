"""Shops table — DB-backed S1-S6 catalog (single source of truth)

Consolidates SHOP_SIGNUP_CATALOG / SHOP_META / SHOP_ROLE / SHOP_ACCESS
(shops.py) + SHOP_DEFS (chain_of_command.py) + RECIPIENT_GROUPS s1-s6
(constants.py) into one table. Canonicalizes the drifted short names/icons.

Revision ID: m1n2o3p4q5r6
Revises: l0m1n2o3p4q5
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa


revision = "m1n2o3p4q5r6"
down_revision = "l0m1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shops",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("short_name", sa.String(length=48), nullable=False),
        sa.Column("icon", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("access_roles", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("has_dashboard", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("key", name="uq_shops_key"),
    )

    shops = sa.table(
        "shops",
        sa.column("key", sa.String), sa.column("name", sa.String),
        sa.column("short_name", sa.String), sa.column("icon", sa.String),
        sa.column("description", sa.Text), sa.column("role", sa.String),
        sa.column("access_roles", sa.String), sa.column("has_dashboard", sa.Boolean),
        sa.column("sort_order", sa.Integer), sa.column("archived", sa.Boolean),
    )
    rows = [
        # key, name (canonical full), short_name, icon, role, access_roles, has_dashboard, order
        ("S1", "S1 — Personnel & Administration", "S1 — Personnel", "📋", "s1", "s1", True, 1,
         "Recruiting pipeline, onboarding, roster and records, promotions, awards and "
         "ribbons, documents, and unit communications. The admin backbone of the company."),
        ("S2", "S2 — Intelligence & Security", "S2 — Intel & Security", "🔍", "s2", "s2", False, 2,
         "Area studies, threat analysis, OPSEC, and security. Produces the intel products "
         "that inform planning and keep the unit sharp."),
        ("S3", "S3 — Operations & Training", "S3 — Ops & Training", "⚔️", "s3", "s3,leader", True, 3,
         "Plans and runs FTXs and training: event building, TRADOC blocks, weapons "
         "qualification, land nav, and attendance tracking. Where the training schedule "
         "comes to life."),
        ("S4", "S4 — Logistics", "S4 — Logistics", "📦", "s4", "s4", False, 4,
         "Supply, equipment, transport, and sustainment. Makes sure the unit has the "
         "gear and support it needs in the field."),
        ("S5", "S5 — Medical", "S5 — Medical", "🩹", "s5", "s5", False, 5,
         "Combat lifesaver and medical training, aid planning, and health/safety "
         "oversight for training events."),
        ("S6", "S6 — Communications", "S6 — Comms", "📡", "s6", "s6", False, 6,
         "Radio (HAM/GMRS) and digital comms, nets and frequency planning, and the "
         "IT/portal infrastructure that ties everything together."),
    ]
    op.bulk_insert(shops, [
        {"key": k, "name": n, "short_name": sn, "icon": ic, "role": r,
         "access_roles": ar, "has_dashboard": hd, "sort_order": o,
         "description": d, "archived": False}
        for (k, n, sn, ic, r, ar, hd, o, d) in rows
    ])


def downgrade():
    op.drop_table("shops")
