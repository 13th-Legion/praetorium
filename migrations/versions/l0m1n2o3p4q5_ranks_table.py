"""Ranks table — DB-backed rank structure (single source of truth)

Consolidates the rank dicts that had drifted across constants.py,
promotions.py, chain_of_command.py, member_edit.py, elections.py, roster.py,
attendance_analytics.py. Seeds the full 19-grade superset.

Revision ID: l0m1n2o3p4q5
Revises: k9l0m1n2o3p4
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa


revision = "l0m1n2o3p4q5"
down_revision = "k9l0m1n2o3p4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ranks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("grade", sa.String(length=4), nullable=False),
        sa.Column("abbr", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=48), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("insignia", sa.String(length=64), nullable=True),
        sa.Column("nc_group", sa.String(length=48), nullable=True),
        sa.Column("pay_category", sa.String(length=16), nullable=False, server_default="enlisted"),
        sa.Column("election_eligible", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("grade", name="uq_ranks_grade"),
    )

    ranks = sa.table(
        "ranks",
        sa.column("grade", sa.String), sa.column("abbr", sa.String),
        sa.column("title", sa.String), sa.column("sort_order", sa.Integer),
        sa.column("insignia", sa.String), sa.column("nc_group", sa.String),
        sa.column("pay_category", sa.String), sa.column("election_eligible", sa.Boolean),
        sa.column("archived", sa.Boolean),
    )
    # Superset from promotions.RANK_ORDER (order) + constants abbr/title +
    # chain_of_command insignia + member_edit nc_group. election_eligible = real
    # grades E-2..O-4 (E-1 recruit excluded; the bogus O-5/O-6/W-5 from the old
    # elections set are simply not created since they don't exist in the unit).
    rows = [
        # grade, abbr, title, order, insignia, nc_group, pay_cat, elig
        ("E-1", "RCT", "Recruit", 1, None, "Rank - Recruit", "enlisted", False),
        ("E-2", "PV2", "Private", 2, "enl_private.svg", "Rank - Enlisted", "enlisted", True),
        ("E-3", "PFC", "Private First Class", 3, "enl_private_first_class.svg", "Rank - Enlisted", "enlisted", True),
        ("E-4", "CPL", "Corporal", 4, "enl_corporal.svg", "Rank - Enlisted", "enlisted", True),
        ("E-5", "SGT", "Sergeant", 5, "enl_sergeant.svg", "Rank - NCO", "nco", True),
        ("E-6", "SSG", "Staff Sergeant", 6, "enl_staff_sergeant.svg", "Rank - NCO", "nco", True),
        ("E-7", "SFC", "Sergeant First Class", 7, "enl_sergeant_first_class.svg", "Rank - NCO", "nco", True),
        ("E-8M", "MSG", "Master Sergeant", 8, "enl_master_sergeant.svg", "Rank - NCO", "nco", True),
        ("E-8", "1SG", "First Sergeant", 9, "enl_first_sergeant.svg", "Rank - NCO", "nco", True),
        ("E-9", "SGM", "Sergeant Major", 10, "enl_sergeant_major.svg", "Rank - NCO", "nco", True),
        ("W-1", "WO1", "Warrant Officer 1", 11, None, "Rank - Officer", "warrant", True),
        ("W-2", "CW2", "Chief Warrant Officer 2", 12, "wo_cw2.svg", "Rank - Officer", "warrant", True),
        ("W-3", "CW3", "Chief Warrant Officer 3", 13, "wo_cw3.svg", "Rank - Officer", "warrant", True),
        ("W-4", "CW4", "Chief Warrant Officer 4", 14, None, "Rank - Officer", "warrant", True),
        ("W-5", "CW5", "Chief Warrant Officer 5", 15, None, "Rank - Officer", "warrant", True),
        ("O-1", "2LT", "Second Lieutenant", 16, "off_second_lieutenant.svg", "Rank - Officer", "officer", True),
        ("O-2", "1LT", "First Lieutenant", 17, "off_first_lieutenant.svg", "Rank - Officer", "officer", True),
        ("O-3", "CPT", "Captain", 18, "off_captain.svg", "Rank - Officer", "officer", True),
        ("O-4", "MAJ", "Major", 19, "off_major.svg", "Rank - Officer", "officer", True),
    ]
    op.bulk_insert(ranks, [
        {"grade": g, "abbr": a, "title": t, "sort_order": o, "insignia": ins,
         "nc_group": ncg, "pay_category": pc, "election_eligible": el, "archived": False}
        for (g, a, t, o, ins, ncg, pc, el) in rows
    ])


def downgrade():
    op.drop_table("ranks")
