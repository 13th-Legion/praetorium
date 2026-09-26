"""Ledger of feedback-form submissions already filed on Deck.

Revision ID: 0016_feedback_deck_cards
Revises: 0015_meal_price_and_slots
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_feedback_deck_cards"
down_revision: Union[str, None] = "0015_meal_price_and_slots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback_deck_cards",
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("submission_id"),
    )


def downgrade() -> None:
    op.drop_table("feedback_deck_cards")
