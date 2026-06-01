"""Add pub_date column to library_document

Revision ID: r9s0t1u2v3w4
Revises: q8r9s0t1u2v3
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "r9s0t1u2v3w4"
down_revision = "q8r9s0t1u2v3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "library_document",
        sa.Column("pub_date", sa.String(length=32), nullable=True),
    )


def downgrade():
    op.drop_column("library_document", "pub_date")
