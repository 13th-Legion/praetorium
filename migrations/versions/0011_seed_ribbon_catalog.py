"""Seed ribbon_catalog with the canonical 13th Legion ribbon/decoration/tab set.

The ribbon catalog is the single source of truth for awardable ribbons, but it
was historically seeded directly in prod (SQL INSERT) and never captured in a
migration — so CI's fresh DB had an empty catalog and any FK into it (e.g. the
mission_leader auto-award) failed. This data migration makes the catalog
reproducible everywhere.

Rows are INSERT ... ON CONFLICT (code) DO NOTHING so it's safe on prod (already
seeded) and on a fresh CI DB (empty).

Revision ID: 0011_seed_ribbon_catalog
Revises: 0010_event_rsvp_no_show
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0011_seed_ribbon_catalog"
down_revision: Union[str, None] = "0010_event_rsvp_no_show"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (code, section, name, precedence, base_points, device_increment, max_devices, is_auto, claimable)
ROWS = [
    ("anni_gold", "tenure", "Anni Stipendiorum — Gold (5 yr)", 1, 15, 0, 0, False, False),
    ("cmd_commendation", "rack", "Commander's Commendation", 1, 20, 10, 0, False, True),
    ("equites", "tab", "Equites", 1, 15, 0, 0, False, True),
    ("gladius_aurum", "dona", "Gladius Aurum (Gold)", 1, 60, 0, 0, False, True),
    ("anni_silver", "tenure", "Anni Stipendiorum — Silver (3 yr)", 2, 9, 0, 0, True, False),
    ("gladius_argentum", "dona", "Gladius Argentum (Silver)", 2, 45, 0, 0, False, True),
    ("meritorious", "rack", "Meritorious Service", 2, 18, 8, 0, False, True),
    ("sharpshooter", "tab", "Sharpshooter", 2, 13, 0, 0, False, True),
    ("anni_bronze", "tenure", "Anni Stipendiorum — Bronze (1 yr)", 3, 3, 0, 0, True, False),
    ("gladius_aes", "dona", "Gladius Aes (Bronze)", 3, 35, 0, 0, False, True),
    ("marksman", "tab", "Marksman", 3, 10, 0, 0, False, True),
    ("real_world_deploy", "rack", "Real World Deployment", 3, 15, 5, 0, False, True),
    ("corona_aurea", "dona", "Corona Aurea", 4, 40, 0, 0, False, True),
    ("founder", "rack", "Founder (Conditor Legionis)", 4, 15, 0, 1, True, True),
    ("sabre", "tab", "Sabre", 4, 8, 0, 0, False, True),
    ("corona_civica", "dona", "Corona Civica", 5, 30, 0, 0, False, True),
    ("officer", "rack", "Officer / Warrant", 5, 12, 0, 1, True, True),
    ("nco", "rack", "NCO", 6, 10, 0, 1, True, True),
    ("phalerae", "dona", "Phalerae", 6, 25, 0, 0, False, True),
    ("leadership", "rack", "Leadership (Billet)", 7, 10, 5, 0, True, True),
    ("mission_leader", "rack", "Mission Leader", 8, 8, 3, 0, False, True),
    ("instructor_ftx", "rack", "Instructor (FTX)", 9, 8, 3, 0, False, True),
    ("instructor_online", "rack", "Instructor (Online)", 10, 5, 2, 0, False, True),
    ("patched", "rack", "Patched Member", 11, 8, 0, 1, True, True),
    ("tradoc", "rack", "TRADOC Completion", 12, 6, 0, 1, True, True),
    ("qual_weapons", "rack", "Qualification (Weapons)", 13, 8, 0, 1, True, True),
    ("qual_landnav", "rack", "Qualification (Land Nav)", 14, 5, 0, 1, True, True),
    ("qual_comms", "rack", "Qualification (Comms)", 15, 5, 0, 1, True, True),
    ("qual_medical", "rack", "Qualification (Medical)", 16, 5, 0, 1, True, True),
    ("ham", "rack", "Amateur Radio", 17, 5, 3, 3, True, True),
    ("perfect_year", "rack", "Perfect Year", 18, 8, 4, 0, True, True),
    ("volunteer", "rack", "Volunteer Service", 19, 5, 5, 0, False, True),
    ("mcftx", "rack", "MCFTX", 20, 6, 2, 0, True, True),
    ("ftx", "rack", "FTX Attendance", 21, 4, 2, 4, True, True),
    ("recruiter", "rack", "Recruiter", 22, 6, 3, 3, False, True),
    ("recruit", "rack", "Recruit Service", 23, 2, 0, 1, True, True),
    ("esprit", "rack", "Esprit de Corps", 24, 5, 2, 0, False, True),
]


def upgrade() -> None:
    # Parameterized single-row INSERT ... ON CONFLICT DO NOTHING (safe on prod).
    # Loop op.execute per row: unambiguous executemany-free, works with asyncpg.
    from sqlalchemy import text

    stmt = text(
        """
        INSERT INTO ribbon_catalog
            (code, section, name, precedence, base_points, device_increment,
             max_devices, is_auto, claimable, active)
        VALUES
            (:code, :section, :name, :precedence, :base_points, :device_increment,
             :max_devices, :is_auto, :claimable, true)
        ON CONFLICT (code) DO NOTHING
        """
    )
    keys = (
        "code", "section", "name", "precedence", "base_points",
        "device_increment", "max_devices", "is_auto", "claimable",
    )
    for r in ROWS:
        op.execute(stmt, dict(zip(keys, r)))


def downgrade() -> None:
    # Remove exactly the seeded codes (leave any prod-added ones alone).
    from sqlalchemy import text

    op.execute(
        text(
            "DELETE FROM ribbon_catalog WHERE code IN ("
            + ", ".join(f"'{c}'" for c, *_ in ROWS)
            + ")"
        )
    )
