"""Create S6 Comms Crypto tables (shackle keys, OTP pads/pages, trench codebooks/entries).

Revision ID: 0027_s6_crypto
Revises: 0026_s6_comms
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0027_s6_crypto"
down_revision: Union[str, None] = "0026_s6_comms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "s6_shackle_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("word", sa.String(16), nullable=False),
        sa.Column("offset", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
    )
    op.create_table(
        "s6_otp_pads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("serial", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("burned", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "s6_otp_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pad_id", sa.Integer(), sa.ForeignKey("s6_otp_pads.id"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("burned", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "s6_trench_codebooks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
    )
    op.create_table(
        "s6_trench_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("codebook_id", sa.Integer(), sa.ForeignKey("s6_trench_codebooks.id"), nullable=False),
        sa.Column("phrase", sa.String(128), nullable=False),
        sa.Column("code_group", sa.String(8), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("s6_trench_entries")
    op.drop_table("s6_trench_codebooks")
    op.drop_table("s6_otp_pages")
    op.drop_table("s6_otp_pads")
    op.drop_table("s6_shackle_keys")
