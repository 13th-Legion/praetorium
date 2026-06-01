"""Add library_document table for Battle Library uploads

Revision ID: q8r9s0t1u2v3
Revises: p7q8r9s0t1u2
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "q8r9s0t1u2v3"
down_revision = "p7q8r9s0t1u2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "library_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("pub_number", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_path", sa.String(length=512), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("uploaded_by", sa.String(length=64), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_library_document_category", "library_document", ["category"]
    )


def downgrade():
    op.drop_index("ix_library_document_category", table_name="library_document")
    op.drop_table("library_document")
