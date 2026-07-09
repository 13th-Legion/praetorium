"""TRADOC Basic/Advanced tiers + move advanced items to Block 100 (PP-244)

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-07-09

Restores a sensible "patched %" meter after Block 100 was added:
  - Blocks 0-4 => tier 'initial' (Basic Training; counts toward patching).
  - Block 100  => tier 'advanced' (Advanced Training; above-and-beyond).
  - Delete empty vestigial Block 5 (TABS).
  - Move Advanced (67) + Expert (68) Land Nav from Block 3 -> Block 100,
    non-optional (advanced tier already excludes them from patching %).
  - Add Tactical Comms + FRO as Block 100 items and remove the matching
    statewide certs (nobody had earned them, so no member data to migrate).
"""
from alembic import op
import sqlalchemy as sa


revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None

ADV_BLOCK = 100
ADV_NAME = "Advanced Qualifications & Tabs"


def upgrade():
    conn = op.get_bind()

    # 1. Tiers: 0-4 initial, 100 advanced.
    conn.execute(sa.text("UPDATE tradoc_blocks SET tier='initial' WHERE number IN (0,1,2,3,4)"))
    conn.execute(sa.text("UPDATE tradoc_blocks SET tier='advanced' WHERE number=100"))

    # 2. Delete empty vestigial Block 5 (TABS) — only if it truly has no items.
    row = conn.execute(sa.text("SELECT COUNT(*) FROM tradoc_items WHERE block=5")).scalar()
    if not row:
        conn.execute(sa.text("DELETE FROM tradoc_blocks WHERE number=5"))

    # 3. Move Advanced (67) + Expert (68) Land Nav to Block 100, non-optional.
    conn.execute(sa.text(
        "UPDATE tradoc_items SET block=:b, block_name=:n, optional=false "
        "WHERE id IN (67,68)"
    ), {"b": ADV_BLOCK, "n": ADV_NAME})

    # 4. Add Tactical Comms + FRO as Block 100 items (idempotent guard by name+block).
    for nm, so in [("Tactical Comms", 5), ("FRO", 6)]:
        exists = conn.execute(sa.text(
            "SELECT COUNT(*) FROM tradoc_items WHERE block=:b AND name=:n"
        ), {"b": ADV_BLOCK, "n": nm}).scalar()
        if not exists:
            conn.execute(sa.text(
                "INSERT INTO tradoc_items "
                "(block, block_name, name, sort_order, optional, archived, doc_type) "
                "VALUES (:b, :bn, :n, :so, false, false, 'none')"
            ), {"b": ADV_BLOCK, "bn": ADV_NAME, "n": nm, "so": so})

    # 5. Remove the statewide certs now represented as TRADOC items (no earners).
    conn.execute(sa.text(
        "DELETE FROM member_certifications WHERE certification_id IN "
        "(SELECT id FROM certifications WHERE name IN ('Tactical Comms','FRO'))"
    ))
    conn.execute(sa.text("DELETE FROM certifications WHERE name IN ('Tactical Comms','FRO')"))


def downgrade():
    conn = op.get_bind()
    # Recreate the two certs (communications category).
    for nm, so in [("Tactical Comms", 99), ("FRO", 104)]:
        exists = conn.execute(sa.text(
            "SELECT COUNT(*) FROM certifications WHERE name=:n"
        ), {"n": nm}).scalar()
        if not exists:
            conn.execute(sa.text(
                "INSERT INTO certifications (name, category, sort_order) "
                "VALUES (:n, 'communications', :so)"
            ), {"n": nm, "so": so})
    # Remove the added Block 100 items.
    conn.execute(sa.text(
        "DELETE FROM tradoc_items WHERE block=:b AND name IN ('Tactical Comms','FRO')"
    ), {"b": ADV_BLOCK})
    # Move Land Nav back to Block 3, optional.
    conn.execute(sa.text(
        "UPDATE tradoc_items SET block=3, block_name='Supplemental Skills', optional=true "
        "WHERE id IN (67,68)"
    ))
    # Recreate Block 5 (TABS) shell.
    exists = conn.execute(sa.text("SELECT COUNT(*) FROM tradoc_blocks WHERE number=5")).scalar()
    if not exists:
        conn.execute(sa.text(
            "INSERT INTO tradoc_blocks (number, name, tier) VALUES (5, 'TABS', 'initial')"
        ))
    # Revert tiers.
    conn.execute(sa.text("UPDATE tradoc_blocks SET tier='initial'"))
    conn.execute(sa.text("UPDATE tradoc_blocks SET tier='advanced' WHERE number=100"))
