"""Create documents table + link document_signatures to it (PP-430).

Revision ID: 0036_documents
Revises: 0035_member_last_contact
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0036_documents"
down_revision: Union[str, None] = "0035_member_last_contact"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_documents_slug"),
    )
    op.create_index("ix_documents_active", "documents", ["active"])
    op.add_column("document_signatures", sa.Column("document_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_document_signatures_document_id",
        "document_signatures", "documents", ["document_id"], ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_document_signatures_document_id", "document_signatures", type_="foreignkey")
    op.drop_column("document_signatures", "document_id")
    op.drop_index("ix_documents_active", table_name="documents")
    op.drop_table("documents")