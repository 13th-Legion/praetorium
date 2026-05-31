"""Add invite_groups to events table

Revision ID: m4n5o6p7q8r9
Revises: l3m4n5o6p7q8
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa

revision = "m4n5o6p7q8r9"
down_revision = "l3m4n5o6p7q8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("invite_groups", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("events", "invite_groups")
