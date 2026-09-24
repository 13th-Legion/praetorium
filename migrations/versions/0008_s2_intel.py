"""S2 Intelligence & Security dashboard — PP-xxx (S2 spec).

Adds the S2-owned tables (IIR reports, challenge/password rotation, training
sites + maps) and the four S2 FTX-responsibility columns on events.

Revision ID: 0008_s2_intel
Revises: 0007_event_duty_assignments
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_s2_intel"
down_revision: Union[str, None] = "0007_event_duty_assignments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── IIR reports ──────────────────────────────────────────────────────────
    op.create_table(
        "s2_iirs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_number", sa.String(length=32), nullable=False),
        sa.Column("dtg", sa.String(length=24), nullable=False),
        sa.Column("country_area", sa.String(length=120), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("reliability_rating", sa.String(length=24), nullable=True),
        sa.Column("credibility", sa.String(length=24), nullable=True),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("assessment", sa.Text(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("dissemination_tier", sa.String(length=16), nullable=False, server_default="command_only"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["members.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_s2_iirs_id", "s2_iirs", ["id"])
    op.create_index("ix_s2_iirs_report_number", "s2_iirs", ["report_number"], unique=True)

    # ── Challenge / password / running-password ─────────────────────────────
    op.create_table(
        "s2_challenge_passwords",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("challenge", sa.String(length=120), nullable=False),
        sa.Column("password", sa.String(length=120), nullable=False),
        sa.Column("running_password", sa.String(length=120), nullable=True),
        sa.Column("valid_from", sa.DateTime(), nullable=True),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_s2_challenge_passwords_id", "s2_challenge_passwords", ["id"])

    # ── Training sites + maps ────────────────────────────────────────────────
    op.create_table(
        "s2_training_sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_s2_training_sites_id", "s2_training_sites", ["id"])
    op.create_index("ix_s2_training_sites_key", "s2_training_sites", ["key"], unique=True)

    op.create_table(
        "s2_training_site_maps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["site_id"], ["s2_training_sites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_s2_training_site_maps_id", "s2_training_site_maps", ["id"])

    # ── S2 FTX-responsibility columns on events ──────────────────────────────
    op.add_column("events", sa.Column("code_words", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("weather_report", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("route", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("route_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "route_url")
    op.drop_column("events", "route")
    op.drop_column("events", "weather_report")
    op.drop_column("events", "code_words")

    op.drop_index("ix_s2_training_site_maps_id", table_name="s2_training_site_maps")
    op.drop_table("s2_training_site_maps")
    op.drop_index("ix_s2_training_sites_key", table_name="s2_training_sites")
    op.drop_index("ix_s2_training_sites_id", table_name="s2_training_sites")
    op.drop_table("s2_training_sites")

    op.drop_index("ix_s2_challenge_passwords_id", table_name="s2_challenge_passwords")
    op.drop_table("s2_challenge_passwords")

    op.drop_index("ix_s2_iirs_report_number", table_name="s2_iirs")
    op.drop_index("ix_s2_iirs_id", table_name="s2_iirs")
    op.drop_table("s2_iirs")
