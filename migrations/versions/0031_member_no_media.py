"""Add no_media flag to members — self-service opt-out of photos/video/social media.

Revision ID: 0031_member_no_media
Revises: 0030_member_status_history
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0031_member_no_media"
down_revision: Union[str, None] = "0030_member_status_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "members",
        sa.Column("no_media", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("members", "no_media")
