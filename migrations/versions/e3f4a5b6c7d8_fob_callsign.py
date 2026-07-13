"""Add fob_callsign to events

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-07-13

"""
from alembic import op
import sqlalchemy as sa


revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("fob_callsign", sa.String(length=32), nullable=True))


def downgrade():
    op.drop_column("events", "fob_callsign")
