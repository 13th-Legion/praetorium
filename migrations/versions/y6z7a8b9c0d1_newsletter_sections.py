"""Newsletter section templates — one-click section library

Revision ID: y6z7a8b9c0d1
Revises: x5y6z7a8b9c0
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa


revision = "y6z7a8b9c0d1"
down_revision = "x5y6z7a8b9c0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "newsletter_section_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=20), nullable=False, server_default="per_issue"),
        sa.Column("default_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("preload", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dynamic_source", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_newsletter_section_templates_key", "newsletter_section_templates", ["key"], unique=True)


def downgrade():
    op.drop_index("ix_newsletter_section_templates_key", table_name="newsletter_section_templates")
    op.drop_table("newsletter_section_templates")
