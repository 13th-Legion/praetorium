"""Add member_weapons_qual table

Revision ID: s0t1u2v3w4x5
Revises: r9s0t1u2v3w4
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "s0t1u2v3w4x5"
down_revision = "r9s0t1u2v3w4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "member_weapons_qual",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("qualified_on", sa.Date(), nullable=True),
        sa.Column("recorded_by", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_member_weapons_qual_member_id", "member_weapons_qual", ["member_id"])
    op.create_index("ix_member_weapons_qual_event_id", "member_weapons_qual", ["event_id"])


def downgrade():
    op.drop_index("ix_member_weapons_qual_event_id", table_name="member_weapons_qual")
    op.drop_index("ix_member_weapons_qual_member_id", table_name="member_weapons_qual")
    op.drop_table("member_weapons_qual")
