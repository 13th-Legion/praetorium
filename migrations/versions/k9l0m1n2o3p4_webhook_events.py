"""Webhook events dedup table — PayPal idempotency/replay protection

Revision ID: k9l0m1n2o3p4
Revises: j8k9l0m1n2o3
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa


revision = "k9l0m1n2o3p4"
down_revision = "j8k9l0m1n2o3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("transaction_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "transaction_id", name="uq_webhook_provider_txn"),
    )
    op.create_index(
        "ix_webhook_events_txn", "webhook_events", ["transaction_id"], unique=False
    )


def downgrade():
    op.drop_index("ix_webhook_events_txn", table_name="webhook_events")
    op.drop_table("webhook_events")
