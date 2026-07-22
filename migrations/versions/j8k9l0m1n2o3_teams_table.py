"""Teams table — DB-backed team metadata (single source of truth)

Replaces hardcoded TEAM_ORDER/TEAM_DESIGNATION/TEAM_TALK_TOKENS/GEO_ZONE_TEAMS
so renames persist across restarts. Seeds the current 7 elements.

Revision ID: j8k9l0m1n2o3
Revises: i7j8k9l0m1n2
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa


revision = "j8k9l0m1n2o3"
down_revision = "i7j8k9l0m1n2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("designation", sa.String(length=2), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("geo_zone_index", sa.Integer(), nullable=True),
        sa.Column("talk_token", sa.String(length=64), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("emoji", sa.String(length=16), nullable=True),
        sa.Column("is_hq", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_unique_constraint("uq_teams_name", "teams", ["name"])

    teams = sa.table(
        "teams",
        sa.column("name", sa.String),
        sa.column("designation", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("geo_zone_index", sa.Integer),
        sa.column("talk_token", sa.String),
        sa.column("color", sa.String),
        sa.column("emoji", sa.String),
        sa.column("is_hq", sa.Boolean),
        sa.column("archived", sa.Boolean),
    )
    # Seed current elements. Aquila = North zone (formerly Alpha, renamed
    # 2026-07-21). Geo zone index: Aquila(N)=0 .. Foxtrot(NW)=5 (bearing slices
    # starting 330°). Headquarters is an organizational overlay, not a geo team.
    op.bulk_insert(
        teams,
        [
            {"name": "Headquarters", "designation": None, "sort_order": 0,
             "geo_zone_index": None, "talk_token": "ogeyhrzd",
             "color": "#d4a537", "emoji": "🏛️", "is_hq": True, "archived": False},
            {"name": "Aquila", "designation": "A", "sort_order": 1,
             "geo_zone_index": 0, "talk_token": "rjdwjoaq",
             "color": "#22c55e", "emoji": "🦅", "is_hq": False, "archived": False},
            {"name": "Bravo", "designation": "B", "sort_order": 2,
             "geo_zone_index": 1, "talk_token": "dazi89uv",
             "color": "#3b82f6", "emoji": "🅱️", "is_hq": False, "archived": False},
            {"name": "Charlie", "designation": "C", "sort_order": 3,
             "geo_zone_index": 2, "talk_token": "z99wo7e4",
             "color": "#ef4444", "emoji": "©️", "is_hq": False, "archived": False},
            {"name": "Delta", "designation": "D", "sort_order": 4,
             "geo_zone_index": 3, "talk_token": "zzw2m7gq",
             "color": "#eab308", "emoji": "🔺", "is_hq": False, "archived": False},
            {"name": "Echo", "designation": "E", "sort_order": 5,
             "geo_zone_index": 4, "talk_token": "s6qbnaae",
             "color": "#f97316", "emoji": "📢", "is_hq": False, "archived": False},
            {"name": "Foxtrot", "designation": "F", "sort_order": 6,
             "geo_zone_index": 5, "talk_token": "ftkdo954",
             "color": "#8b5cf6", "emoji": "🦊", "is_hq": False, "archived": False},
        ],
    )


def downgrade():
    op.drop_constraint("uq_teams_name", "teams", type_="unique")
    op.drop_table("teams")
