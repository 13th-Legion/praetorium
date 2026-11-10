"""Create S6 Comms dashboard tables (EmComm Plan, channels, net schedule, radios).

Revision ID: 0026_s6_comms
Revises: 0025_separation_internal_memo
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0026_s6_comms"
down_revision: Union[str, None] = "0025_separation_internal_memo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "s6_emcomm_plan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("draft_content", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=True),
    )
    op.create_table(
        "s6_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("channel_no", sa.String(16), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("alias", sa.String(64), nullable=False),
        sa.Column("rx_freq", sa.String(16), nullable=False),
        sa.Column("tx_freq", sa.String(16), nullable=False),
        sa.Column("tx_power", sa.String(16), nullable=False),
        sa.Column("dec_tone", sa.String(32), nullable=False),
        sa.Column("enc_tone", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
    )
    op.create_table(
        "s6_net_schedule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day_of_week", sa.String(16), nullable=False),
        sa.Column("start_time", sa.String(16), nullable=False),
        sa.Column("frequency", sa.String(32), nullable=False),
        sa.Column("alias", sa.String(64), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
    )
    op.create_table(
        "s6_radios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("radio_type", sa.String(64), nullable=False),
        sa.Column("serial", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("checked_out_to", sa.String(128), nullable=True),
        sa.Column("checked_out_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("s6_radios")
    op.drop_table("s6_net_schedule")
    op.drop_table("s6_channels")
    op.drop_table("s6_emcomm_plan")
