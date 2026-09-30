"""Point the Block 3 comms subjects at their lesson pages.

Revision ID: 0017_comms_tradoc_pages
Revises: 0016_feedback_deck_cards
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017_comms_tradoc_pages"
down_revision: Union[str, None] = "0016_feedback_deck_cards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PAGES = (
    ("Radio Familiarity", "Radio Familiarity", "/training/tradoc/radio-familiarity"),
    ("Net Etiquette", "Net Etiquette", "/training/tradoc/net-etiquette"),
    ("Reports", "Reports", "/training/tradoc/reports"),
)


def upgrade() -> None:
    for name, title, url in _PAGES:
        op.execute(
            "UPDATE tradoc_items SET doc_type = 'page', "
            f"doc_title = '{title}', doc_url = '{url}' "
            f"WHERE block = 3 AND name = '{name}'"
        )


def downgrade() -> None:
    for name, _title, _url in _PAGES:
        op.execute(
            "UPDATE tradoc_items SET doc_type = 'none', doc_title = NULL, doc_url = NULL "
            f"WHERE block = 3 AND name = '{name}'"
        )
