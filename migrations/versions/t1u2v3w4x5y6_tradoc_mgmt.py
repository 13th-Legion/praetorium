"""TRADOC management: tradoc_blocks table + doc fields + archived flags

Revision ID: t1u2v3w4x5y6
Revises: s0t1u2v3w4x5
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa


revision = "t1u2v3w4x5y6"
down_revision = "s0t1u2v3w4x5"
branch_labels = None
depends_on = None


# Backfill map: TradocItem.name -> doc fields (mirrors legacy TRADOC_DOCS dict).
# type 'page' = legacy hardcoded HTML template route, 'pdf' = direct PDF, 'external' = off-site.
DOC_BACKFILL = {
    "Drill & Ceremony":             ("page", "Drill & Ceremony SOP",        "/training/tradoc/drill-ceremony"),
    "Gear Review":                  ("pdf",  "TSM Uniform SOP",             "/static/tradoc/uniform-sop.pdf"),
    "Basic Rifle Marksmanship":     ("page", "BRM Training Guide",          "/training/tradoc/brm"),
    "Rifle Qualification":          ("page", "Basic Course of Fire",        "/training/tradoc/course-of-fire"),
    "Shooting Drills":              ("page", "Basic Course of Fire",        "/training/tradoc/course-of-fire"),
    "Use of Force":                 ("page", "Use of Force SOP",            "/training/tradoc/use-of-force"),
    "Basic Land Navigation":        ("page", "Basic Land Navigation",       "/training/tradoc/landnav-basic"),
    "Intermediate Land Navigation": ("page", "Intermediate Land Navigation","/training/tradoc/landnav-intermediate"),
    "Advanced Land Navigation":     ("page", "Advanced Land Navigation",    "/training/tradoc/landnav-advanced"),
    "Expert Land Navigation":       ("page", "Expert Land Navigation",      "/training/tradoc/landnav-expert"),
    "Recon 101":                    ("page", "Field Reconnaissance (13LP 2-1)", "/training/tradoc/recon"),
}


def upgrade():
    # 1. tradoc_blocks table
    op.create_table(
        "tradoc_blocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number", name="uq_tradoc_blocks_number"),
    )

    # 2. new columns on tradoc_items
    op.add_column("tradoc_items", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tradoc_items", sa.Column("doc_type", sa.String(length=16), nullable=False, server_default="none"))
    op.add_column("tradoc_items", sa.Column("doc_title", sa.String(length=160), nullable=True))
    op.add_column("tradoc_items", sa.Column("doc_url", sa.Text(), nullable=True))
    op.add_column("tradoc_items", sa.Column("doc_body", sa.Text(), nullable=True))

    conn = op.get_bind()

    # 3. seed tradoc_blocks from existing distinct (block, block_name) pairs
    rows = conn.execute(sa.text(
        "SELECT DISTINCT block, block_name FROM tradoc_items ORDER BY block"
    )).fetchall()
    for block_num, block_name in rows:
        # sort_order: keep Every-FTX (0) last by giving it a high sort, blocks 1-4 natural
        sort = 99 if block_num == 0 else block_num
        conn.execute(
            sa.text(
                "INSERT INTO tradoc_blocks (number, name, sort_order, archived) "
                "VALUES (:n, :nm, :s, false)"
            ),
            {"n": block_num, "nm": block_name, "s": sort},
        )

    # 4. backfill doc fields from legacy DOC_BACKFILL map
    for name, (dtype, dtitle, durl) in DOC_BACKFILL.items():
        conn.execute(
            sa.text(
                "UPDATE tradoc_items SET doc_type=:t, doc_title=:ti, doc_url=:u WHERE name=:nm"
            ),
            {"t": dtype, "ti": dtitle, "u": durl, "nm": name},
        )


def downgrade():
    op.drop_column("tradoc_items", "doc_body")
    op.drop_column("tradoc_items", "doc_url")
    op.drop_column("tradoc_items", "doc_title")
    op.drop_column("tradoc_items", "doc_type")
    op.drop_column("tradoc_items", "archived")
    op.drop_table("tradoc_blocks")
