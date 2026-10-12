"""Add notifications.source_id so deletes can purge the rows a record fanned out.

Revision ID: 0021_notification_source_id
Revises: 0020_phalerae_metals
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0021_notification_source_id"
down_revision: Union[str, None] = "0020_phalerae_metals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("source_id", sa.String(64), nullable=True))
    op.create_index(
        "ix_notifications_source_id", "notifications", ["source_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_source_id", table_name="notifications")
    op.drop_column("notifications", "source_id")
