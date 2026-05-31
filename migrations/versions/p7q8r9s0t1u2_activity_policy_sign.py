"""Add activity_policy signature fields to members

Revision ID: p7q8r9s0t1u2
Revises: o6p7q8r9s0t1
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa

revision = "p7q8r9s0t1u2"
down_revision = "o6p7q8r9s0t1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("members", sa.Column("activity_policy_signed_at", sa.DateTime(), nullable=True))
    op.add_column("members", sa.Column("activity_policy_ip_address", sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column("members", "activity_policy_ip_address")
    op.drop_column("members", "activity_policy_signed_at")
