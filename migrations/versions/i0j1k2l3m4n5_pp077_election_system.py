"""PP-077: CO Election System — five tables for anonymous elections.

Revision ID: i0j1k2l3m4n5
Revises: h9i0j1k2l3m4
Create Date: 2026-04-08

Tables created:
  elections                    — election lifecycle
  election_nominations         — nominees (no nominator_id)
  election_nomination_receipts — nomination usage receipts (no nominee link)
  election_ballots             — anonymous ballot box (no voter_id)
  election_voter_roll          — who voted (no ballot link)
"""

from alembic import op
import sqlalchemy as sa


revision = "i0j1k2l3m4n5"
down_revision = "h9i0j1k2l3m4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── elections ─────────────────────────────────────────────────────────────
    op.create_table(
        "elections",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False, server_default="nominations"),
        sa.Column("nominations_open", sa.DateTime(), nullable=True),
        sa.Column("nominations_close", sa.DateTime(), nullable=True),
        sa.Column("voting_open", sa.DateTime(), nullable=True),
        sa.Column("voting_close", sa.DateTime(), nullable=True),
        sa.Column("quorum_pct", sa.Integer(), nullable=False, server_default="75"),
        sa.Column("eligible_count", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("runoff_of", sa.Integer(), sa.ForeignKey("elections.id"), nullable=True),
    )
    op.create_index("ix_elections_phase", "elections", ["phase"])

    # ── election_nominations ──────────────────────────────────────────────────
    # NO nominator_id — nominations are anonymous
    op.create_table(
        "election_nominations",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("election_id", sa.Integer(), sa.ForeignKey("elections.id"), nullable=False),
        sa.Column("nominee_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("nominated_at", sa.DateTime(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("election_id", "nominee_id", name="uq_election_nominee"),
    )
    op.create_index("ix_election_nominations_election", "election_nominations", ["election_id"])

    # ── election_nomination_receipts ──────────────────────────────────────────
    # NO link to which nominee was selected — only that a nomination was used
    op.create_table(
        "election_nomination_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("election_id", sa.Integer(), sa.ForeignKey("elections.id"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("nominated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("election_id", "member_id", name="uq_election_nominator"),
    )
    op.create_index(
        "ix_election_nomination_receipts_election",
        "election_nomination_receipts",
        ["election_id"],
    )

    # ── election_ballots ──────────────────────────────────────────────────────
    # NO voter_id — anonymous ballot box
    # cast_at is rounded to nearest hour before storage (app-side)
    op.create_table(
        "election_ballots",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("election_id", sa.Integer(), sa.ForeignKey("elections.id"), nullable=False),
        sa.Column("nominee_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("cast_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_election_ballots_election", "election_ballots", ["election_id"])
    op.create_index("ix_election_ballots_nominee", "election_ballots", ["nominee_id"])

    # ── election_voter_roll ───────────────────────────────────────────────────
    # NO link to election_ballots — tracks who voted, not how
    op.create_table(
        "election_voter_roll",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("election_id", sa.Integer(), sa.ForeignKey("elections.id"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("voted_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("election_id", "member_id", name="uq_election_voter"),
    )
    op.create_index("ix_election_voter_roll_election", "election_voter_roll", ["election_id"])


def downgrade() -> None:
    op.drop_table("election_voter_roll")
    op.drop_table("election_ballots")
    op.drop_table("election_nomination_receipts")
    op.drop_table("election_nominations")
    op.drop_table("elections")
