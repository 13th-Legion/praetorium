"""Add fireteam column to members for manual squad/fireteam assignment.

Revision ID: 0024_member_fireteam
Revises: 0023_urban_evasion
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0024_member_fireteam"
down_revision: Union[str, None] = "0023_urban_evasion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("members", sa.Column("fireteam", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("members", "fireteam")
