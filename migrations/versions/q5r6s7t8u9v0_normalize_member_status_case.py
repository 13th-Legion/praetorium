"""Normalize member.status to lowercase (bug #10 safety net)

Analytics/awards/ribbons queries filter status lowercase-only, while some
event paths defensively matched both cases. Prod data is already all-lowercase,
but a legacy/future import with a capitalized status would be silently excluded
from analytics while still receiving WARNOs. This one-time UPDATE + the removal
of the capitalized query variants (in code) make lowercase the single form.

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-07-22
"""
from alembic import op


revision = "q5r6s7t8u9v0"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: lowercase any capitalized status values.
    op.execute("UPDATE members SET status = LOWER(status) WHERE status <> LOWER(status)")


def downgrade():
    # No-op: we don't restore mixed-case status (there was no reliable original
    # casing, and lowercase is the canonical form).
    pass
