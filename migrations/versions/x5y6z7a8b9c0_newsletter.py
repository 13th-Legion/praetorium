"""Legionary Dispatch newsletter — newsletters, images, attachments

Revision ID: x5y6z7a8b9c0
Revises: w4x5y6z7a8b9
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa


revision = "x5y6z7a8b9c0"
down_revision = "w4x5y6z7a8b9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "newsletters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("crest_key", sa.String(length=64), nullable=False, server_default="standard"),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("groups_csv", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("archive_path", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_by_name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_newsletters_status", "newsletters", ["status"], unique=False)
    op.create_index("ix_newsletters_scheduled_at", "newsletters", ["scheduled_at"], unique=False)

    op.create_table(
        "newsletter_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("newsletter_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("orig_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(length=120), nullable=False, server_default="image/png"),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["newsletter_id"], ["newsletters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_newsletter_images_newsletter_id", "newsletter_images", ["newsletter_id"], unique=False)

    op.create_table(
        "newsletter_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("newsletter_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("orig_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(length=120), nullable=False, server_default="application/octet-stream"),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["newsletter_id"], ["newsletters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_newsletter_attachments_newsletter_id", "newsletter_attachments", ["newsletter_id"], unique=False)


def downgrade():
    op.drop_index("ix_newsletter_attachments_newsletter_id", table_name="newsletter_attachments")
    op.drop_table("newsletter_attachments")
    op.drop_index("ix_newsletter_images_newsletter_id", table_name="newsletter_images")
    op.drop_table("newsletter_images")
    op.drop_index("ix_newsletters_scheduled_at", table_name="newsletters")
    op.drop_index("ix_newsletters_status", table_name="newsletters")
    op.drop_table("newsletters")
