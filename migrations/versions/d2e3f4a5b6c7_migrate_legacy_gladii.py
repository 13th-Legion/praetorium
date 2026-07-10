"""Migrate legacy member_awards Gladii -> member_ribbons Dona (PP-247)

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-09

The 4 legacy Gladii in member_awards predate the tiered Dona Militaria system.
Per Cav's classification:
  - "Ictus Primus" (semi-annual shooting-competition winner) -> gladius_aes (bronze)
  - "Via Equitis"  (completed the Via Equitis component, first back to base)
                   -> gladius_argentum (silver)  [decoration only, NOT the Equites tab]

Preserves original reason / awarded_by / awarded_at. Idempotent: only inserts a
member_ribbons row when one does not already exist for that (member, code).
Legacy member_awards rows are LEFT IN PLACE (audit trail); the profile Dona
renderer reads member_ribbons going forward.
"""
from alembic import op
import sqlalchemy as sa


revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def _classify(reason: str) -> str | None:
    r = (reason or "").lower()
    if "ictus primus" in r:
        return "gladius_aes"
    if "via equitis" in r:
        return "gladius_argentum"
    return None


def upgrade():
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, member_id, award_name, reason, awarded_by, awarded_at "
        "FROM member_awards"
    )).fetchall()

    for r in rows:
        code = _classify(r.reason)
        if not code:
            # Unknown legacy award — skip (leave for manual review); don't guess.
            continue
        exists = conn.execute(sa.text(
            "SELECT 1 FROM member_ribbons WHERE member_id=:mid AND ribbon_code=:code"
        ), {"mid": r.member_id, "code": code}).first()
        if exists:
            continue
        conn.execute(sa.text(
            "INSERT INTO member_ribbons "
            "(member_id, ribbon_code, device_count, awarded_at, awarded_by, reason, source) "
            "VALUES (:mid, :code, 0, :at, :by, :reason, 'manual')"
        ), {
            "mid": r.member_id,
            "code": code,
            "at": r.awarded_at,
            "by": r.awarded_by,
            "reason": r.reason,
        })


def downgrade():
    # Remove only the rows this migration would have created (the two mapped codes,
    # source='manual', matching a legacy member_awards reason).
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM member_ribbons mr USING member_awards ma "
        "WHERE mr.member_id = ma.member_id AND mr.source='manual' "
        "AND ("
        "  (mr.ribbon_code='gladius_aes'      AND lower(ma.reason) LIKE '%ictus primus%') OR "
        "  (mr.ribbon_code='gladius_argentum' AND lower(ma.reason) LIKE '%via equitis%')"
        ")"
    ))
