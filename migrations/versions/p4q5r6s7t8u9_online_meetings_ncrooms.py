"""Online meetings: events.meeting_mode + events.talk_token + nc_rooms table

Adds an online/physical distinction to events and a curated NC Talk room
directory to power the online-meeting channel dropdown + auto join link.

Revision ID: p4q5r6s7t8u9
Revises: o3p4q5r6s7t8
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa


revision = "p4q5r6s7t8u9"
down_revision = "o3p4q5r6s7t8"
branch_labels = None
depends_on = None


def upgrade():
    # ── events: meeting mode + talk room ──────────────────────────────────────
    op.add_column("events", sa.Column("meeting_mode", sa.String(length=16),
                                      nullable=False, server_default="physical"))
    op.add_column("events", sa.Column("talk_token", sa.String(length=64), nullable=True))

    # ── nc_rooms directory ────────────────────────────────────────────────────
    op.create_table(
        "nc_rooms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=96), nullable=False),
        sa.Column("meeting_selectable", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("token", name="uq_nc_rooms_token"),
    )
    nc_rooms = sa.table(
        "nc_rooms",
        sa.column("token", sa.String), sa.column("name", sa.String),
        sa.column("meeting_selectable", sa.Boolean), sa.column("sort_order", sa.Integer),
    )
    rows = [
        ("em8hs3rm", "T2 · Skunk Works", 1),
        ("rjdwjoaq", "T1 · Aquila", 2),
        ("dazi89uv", "T1 · Bravo", 3),
        ("z99wo7e4", "T1 · Charlie", 4),
        ("zzw2m7gq", "T1 · Delta", 5),
        ("s6qbnaae", "T1 · Echo", 6),
        ("ftkdo954", "T1 · Foxtrot", 7),
        ("jf5p44k9", "T2 · Training", 8),
        ("atnd3vgf", "T1 · Announcements", 9),
        ("td853igi", "T2 · Digital Infrastructure", 10),
        ("6pp5pf6c", "T2 · Newsletter", 11),
        ("7jrpakx2", "T1 · Command", 12),
        ("i5qf7jyy", "T1 · S2 - Intelligence & Security", 13),
        ("ogeyhrzd", "T1 · HQ", 14),
        ("qxzmz85j", "T1 · S3 - Training & Ops", 15),
        ("naqbeqn4", "T1 · S5 - Medical", 16),
        ("ekgvfbkf", "T1 · S4 - Logistics", 17),
        ("gixodxm2", "T1 - HR Office", 18),
    ]
    op.bulk_insert(nc_rooms, [
        {"token": t, "name": n, "meeting_selectable": True, "sort_order": o}
        for (t, n, o) in rows
    ])


def downgrade():
    op.drop_table("nc_rooms")
    op.drop_column("events", "talk_token")
    op.drop_column("events", "meeting_mode")
