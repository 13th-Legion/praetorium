"""Keep one comment per ribbon award instead of one comment per ribbon.

Revision ID: 0019_ribbon_citations
Revises: 0017_comms_tradoc_pages
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_ribbon_citations"
down_revision: Union[str, None] = "0017_comms_tradoc_pages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ribbon_citations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_ribbon_id", sa.Integer(), sa.ForeignKey("member_ribbons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("award_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("awarded_at", sa.DateTime(), nullable=True),
        sa.Column("awarded_by", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("member_ribbon_id", "award_index", name="uq_ribbon_citation"),
    )
    op.create_index("ix_ribbon_citations_member_ribbon_id", "ribbon_citations", ["member_ribbon_id"])
    # The text we still have is the latest write. It belongs on the latest award.
    op.execute(
        """
        INSERT INTO ribbon_citations (member_ribbon_id, award_index, reason, awarded_at, awarded_by)
        SELECT id, COALESCE(device_count, 0), reason, awarded_at, awarded_by
        FROM member_ribbons
        WHERE reason IS NOT NULL AND reason <> ''
        """
    )


def downgrade() -> None:
    op.drop_index("ix_ribbon_citations_member_ribbon_id", table_name="ribbon_citations")
    op.drop_table("ribbon_citations")
