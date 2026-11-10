"""Add internal separation memo fields (member + separation_log).

Revision ID: 0025_separation_internal_memo
Revises: 0024_member_fireteam
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0025_separation_internal_memo"
down_revision: Union[str, None] = "0024_member_fireteam"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("members", sa.Column("separation_internal_memo", sa.Text(), nullable=True))
    op.add_column("separation_log", sa.Column("internal_memo", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("separation_log", "internal_memo")
    op.drop_column("members", "separation_internal_memo")
