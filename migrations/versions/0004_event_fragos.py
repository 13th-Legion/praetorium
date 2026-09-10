"""Unlimited numbered FRAGOs per event.

`events.fragord_issued_at` was the only FRAGO state that existed: a single
timestamp, set by an endpoint that did nothing else. That shape had two
consequences, both hit in production on 2026-09-10:

  * A FRAGO could be issued exactly once per event. The event_detail pipeline
    renders `if not warno / elif not opord / elif not fragord / else complete`,
    so once the timestamp was set the button disappeared and there was no UI
    path to a second FRAGO -- for an FTX whose schedule moves more than once,
    that is the normal case, not an edge case.
  * The order carried no content and triggered no distribution. The CO issued a
    FRAGO for event 73 at 14:08:06Z, got HTTP 200, the column was written, and
    not one member was emailed, posted to, or notified.

This adds a child table so FRAGOs are numbered, repeatable, carry the substance
of the change, and record whether distribution actually happened.
`events.fragord_issued_at` is kept and maintained as a mirror of the newest
FRAGO's timestamp, so the existing banner/chip rendering is untouched.

The backfill turns any existing orphaned timestamp into FRAGO 1 rather than
discarding it. Its body is left NULL because the original text was never
captured anywhere -- there is nothing to recover, and inventing text would be
worse than an honest gap.

Revision ID: 0004_event_fragos
Revises: 0003_separation_cleanup
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_event_fragos"
down_revision: Union[str, None] = "0003_separation_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_fragos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("issued_by", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("email_count", sa.Integer(), nullable=True),
        sa.Column("email_failed", sa.Integer(), nullable=True),
        sa.Column("talk_posted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "number", name="uq_event_fragos_event_number"),
    )
    op.create_index("ix_event_fragos_event_id", "event_fragos", ["event_id"])

    # Backfill: every event that already carries a fragord_issued_at becomes FRAGO 1.
    # Written as SQL rather than ORM so the migration does not depend on model state.
    op.execute(
        """
        INSERT INTO event_fragos
            (event_id, number, subject, body, issued_by, issued_at,
             talk_posted, notified, created_at)
        SELECT
            e.id,
            1,
            'FRAGO 1 (recorded before FRAGO tracking existed)',
            NULL,
            'system',
            e.fragord_issued_at,
            FALSE,
            FALSE,
            e.fragord_issued_at
        FROM events e
        WHERE e.fragord_issued_at IS NOT NULL
        """
    )


def downgrade() -> None:
    # events.fragord_issued_at was never dropped, so the pre-migration state is
    # still fully represented by that column; dropping this table loses only the
    # per-FRAGO content added after the upgrade.
    op.drop_index("ix_event_fragos_event_id", table_name="event_fragos")
    op.drop_table("event_fragos")
