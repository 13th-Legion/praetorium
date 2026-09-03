"""Track Talk eviction + device-token revocation on separations.

Offboarding could report success while silently leaving an ex-member in their
Nextcloud groups and Talk rooms with live device tokens. RCT Rankin sat that
way for two months, his Android client throwing a 503 every ~20 minutes.

separation_log already had nc_account_disabled / groups_removed /
portal_access_revoked, but nothing recorded the two steps that actually keep a
disabled account noisy:
  * Disabling an NC account does NOT evict the user from Talk conversations.
  * Disabling an NC account does NOT revoke existing device auth tokens.

Revision ID: 0003_separation_cleanup
Revises: 0002_reconcile
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_separation_cleanup"
down_revision: Union[str, None] = "0002_reconcile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "separation_log",
        sa.Column("talk_removed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "separation_log",
        sa.Column("tokens_revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("separation_log", "tokens_revoked")
    op.drop_column("separation_log", "talk_removed")
