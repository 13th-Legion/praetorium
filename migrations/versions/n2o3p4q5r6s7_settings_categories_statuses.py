"""app_settings + event_categories + member_statuses tables (config SSoT)

Wave C of the P1 single-source-of-truth refactor: a generic key/value settings
table for scalar dials, plus lookup tables for event categories and member
statuses.

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa


revision = "n2o3p4q5r6s7"
down_revision = "m1n2o3p4q5r6"
branch_labels = None
depends_on = None


def upgrade():
    # ── app_settings ────────────────────────────────────────────────────────
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("value_type", sa.String(length=8), nullable=False, server_default="str"),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("key", name="uq_app_settings_key"),
    )
    app_settings = sa.table(
        "app_settings",
        sa.column("key", sa.String), sa.column("value", sa.Text),
        sa.column("value_type", sa.String), sa.column("label", sa.String),
        sa.column("category", sa.String),
    )
    op.bulk_insert(app_settings, [
        {"key": "geo_center_lat", "value": "32.7512", "value_type": "float",
         "label": "Geo center latitude", "category": "geo"},
        {"key": "geo_center_lng", "value": "-97.0457", "value_type": "float",
         "label": "Geo center longitude", "category": "geo"},
        {"key": "geo_zone_start", "value": "330", "value_type": "int",
         "label": "Geo zone start bearing (°)", "category": "geo"},
        {"key": "geo_zone_size", "value": "60", "value_type": "int",
         "label": "Geo zone size (°)", "category": "geo"},
        {"key": "warno_talk_room", "value": "atnd3vgf", "value_type": "str",
         "label": "WARNO/Announcements Talk room token", "category": "comms"},
        {"key": "max_shops", "value": "2", "value_type": "int",
         "label": "Max shops a member may hold", "category": "shops"},
        {"key": "tenure_points_gold", "value": "15", "value_type": "int",
         "label": "Tenure ribbon points — gold", "category": "ribbons"},
        {"key": "tenure_points_silver", "value": "9", "value_type": "int",
         "label": "Tenure ribbon points — silver", "category": "ribbons"},
        {"key": "tenure_points_bronze", "value": "3", "value_type": "int",
         "label": "Tenure ribbon points — bronze", "category": "ribbons"},
        {"key": "ftx_device_thresholds", "value": "[5, 10, 25, 50]", "value_type": "json",
         "label": "FTX attendance device thresholds", "category": "ribbons"},
    ])

    # ── event_categories ──────────────────────────────────────────────────────
    op.create_table(
        "event_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=48), nullable=False),
        sa.Column("icon", sa.String(length=16), nullable=False, server_default="📅"),
        sa.Column("rsvp_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("warno_lead_days", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("code", name="uq_event_categories_code"),
    )
    ec = sa.table(
        "event_categories",
        sa.column("code", sa.String), sa.column("label", sa.String),
        sa.column("icon", sa.String), sa.column("rsvp_default", sa.Boolean),
        sa.column("warno_lead_days", sa.Integer), sa.column("sort_order", sa.Integer),
    )
    # code, label, icon, rsvp_default, warno_lead_days, order
    cats = [
        ("ftx", "FTX", "🏕️", True, 14, 1),
        ("mcftx", "MCFTX", "⚔️", True, 28, 2),
        ("online_training", "Online Training", "💻", False, None, 3),
        ("meeting", "Meeting", "🎖️", False, None, 4),
        ("external_training", "External Training", "🎓", True, None, 5),
        ("family_day", "Family Day", "👨‍👩‍👧‍👦", True, None, 6),
        ("social", "Social", "🤝", True, None, 7),
        ("volunteering", "Volunteering", "🫡", True, None, 8),
        ("other", "Other", "📅", False, None, 9),
    ]
    op.bulk_insert(ec, [
        {"code": c, "label": l, "icon": i, "rsvp_default": r,
         "warno_lead_days": w, "sort_order": o}
        for (c, l, i, r, w, o) in cats
    ])

    # ── member_statuses ────────────────────────────────────────────────────────
    op.create_table(
        "member_statuses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False, server_default="#888888"),
        sa.Column("lifecycle", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("code", name="uq_member_statuses_code"),
    )
    ms = sa.table(
        "member_statuses",
        sa.column("code", sa.String), sa.column("label", sa.String),
        sa.column("color", sa.String), sa.column("lifecycle", sa.String),
        sa.column("sort_order", sa.Integer),
    )
    # code, label, color, lifecycle, order
    statuses = [
        ("recruit", "Recruit", "#42a5f5", "in_progress", 1),
        ("active", "Active", "#4caf50", "active", 2),
        ("inactive", "Inactive", "#888888", "left", 3),
        ("separated", "Separated", "#f39c12", "left", 4),
        ("blacklisted", "Blacklisted", "#ef5350", "left", 5),
    ]
    op.bulk_insert(ms, [
        {"code": c, "label": l, "color": col, "lifecycle": lc, "sort_order": o}
        for (c, l, col, lc, o) in statuses
    ])


def downgrade():
    op.drop_table("member_statuses")
    op.drop_table("event_categories")
    op.drop_table("app_settings")
