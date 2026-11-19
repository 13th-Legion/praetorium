"""Add member.last_contact for the activity-policy no-contact track.

Revision ID: 0035_member_last_contact
Revises: 0034_company_routing_state_s1
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0035_member_last_contact"
down_revision: Union[str, None] = "0034_company_routing_state_s1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("members", sa.Column("last_contact", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("members", "last_contact")
