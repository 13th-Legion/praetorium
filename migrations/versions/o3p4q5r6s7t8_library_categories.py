"""library_categories table — publication taxonomy SSoT

Wave D of the P1 refactor. Also removes the dead TRADOC_DOCS map from code (the
tradoc_items.doc_* columns already superseded it) — that's a code-only change;
this migration just adds the library_categories lookup.

Revision ID: o3p4q5r6s7t8
Revises: n2o3p4q5r6s7
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa


revision = "o3p4q5r6s7t8"
down_revision = "n2o3p4q5r6s7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "library_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("icon", sa.String(length=16), nullable=False, server_default="📚"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("code", name="uq_library_categories_code"),
    )
    lc = sa.table(
        "library_categories",
        sa.column("code", sa.String), sa.column("category", sa.String),
        sa.column("icon", sa.String), sa.column("sort_order", sa.Integer),
    )
    rows = [
        ("13LG", "13LG Publications", "⚔️", 1),
        ("TSM", "TSM Publications", "🛡️", 2),
        ("FM", "Field Manuals (FM)", "📗", 3),
        ("TC", "Training Circulars (TC)", "📘", 4),
        ("ATP", "Army Techniques Publications (ATP)", "📙", 5),
        ("TM", "Technical Manuals (TM)", "📕", 6),
        ("Other", "Other Publications", "📚", 7),
    ]
    op.bulk_insert(lc, [
        {"code": c, "category": cat, "icon": i, "sort_order": o}
        for (c, cat, i, o) in rows
    ])


def downgrade():
    op.drop_table("library_categories")
