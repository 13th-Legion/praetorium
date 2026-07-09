"""Shop reporting map for chain-of-command chart (PP-243)

Revision ID: a8b9c0d1e2f3
Revises: z7a8b9c0d1e2
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa


revision = "a8b9c0d1e2f3"
down_revision = "z7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shop_reporting",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_key", sa.String(length=8), nullable=False),
        sa.Column("reports_to_member_id", sa.Integer(), nullable=True),
        sa.Column("command_led", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.String(length=120), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["reports_to_member_id"], ["members.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("shop_key", name="uq_shop_reporting_shop_key"),
    )

    # Seed the six shops. reports_to is resolved by nc_username lookup so the
    # seed is robust to id drift. Command-led shops (S3=CO, S6=1SG) get no target.
    #   S1 Admin   -> 1SG Eastman  (jessica.eastman)
    #   S2 Intel   -> CO Kavadas   (levi.kavadas)
    #   S3 Ops     -> command-led (CO is S3 lead)
    #   S4 Log     -> XO Camp      (cody.camp)
    #   S5 Med     -> 1SG Eastman  (jessica.eastman)
    #   S6 Comms   -> command-led (1SG is S6 lead)
    conn = op.get_bind()

    def mid(nc_username):
        row = conn.execute(
            sa.text("SELECT id FROM members WHERE nc_username = :u"),
            {"u": nc_username},
        ).first()
        return row[0] if row else None

    eastman = mid("jessica.eastman")
    kavadas = mid("levi.kavadas")
    camp = mid("cody.camp")

    seed = [
        ("S1", eastman, False, None),
        ("S2", kavadas, False, None),
        ("S3", None, True, "CO is S3 Lead — top of chain"),
        ("S4", camp, False, None),
        ("S5", eastman, False, None),
        ("S6", None, True, "1SG is S6 Lead — top of chain"),
    ]
    for shop_key, rid, cmd_led, note in seed:
        conn.execute(
            sa.text(
                "INSERT INTO shop_reporting "
                "(shop_key, reports_to_member_id, command_led, note, updated_at) "
                "VALUES (:k, :r, :c, :n, now())"
            ),
            {"k": shop_key, "r": rid, "c": cmd_led, "n": note},
        )


def downgrade():
    op.drop_table("shop_reporting")
