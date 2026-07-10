"""Ribbon system: catalog + member_ribbons + seed (PP-247)

Revision ID: c1d2e3f4a5b6
Revises: b9c0d1e2f3a4
Create Date: 2026-07-09

Creates the ribbon_catalog (single source of truth for every awardable
ribbon/decoration/tab, with precedence + promotion-point values) and
member_ribbons (per-member awards with device counts + provenance).

Seeds the catalog from RIBBON_SYSTEM_SPEC.md:
  - 6 Dona Militaria decorations
  - 23 ribbon-rack achievements (precedence-ordered)
  - 4 qualification tabs
  - 3 Anni Stipendiorum tenure discs (point values only; discs are computed
    from join_date at render time)

Codes match the art filename stems in app/static/img/ribbons/.
"""
from alembic import op
import sqlalchemy as sa


revision = "c1d2e3f4a5b6"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


# ── Catalog seed ─────────────────────────────────────────────────────────────
# (code, name, section, precedence, base_pts, dev_incr, max_dev, image, is_auto, claimable, description)
DONA = [
    ("gladius_aurum",    "Gladius Aurum (Gold)",     "dona", 1, 60, 0, 0, "dona/gladius_aurum.png",    False, True,  "Highest valor/merit decoration."),
    ("gladius_argentum", "Gladius Argentum (Silver)","dona", 2, 45, 0, 0, "dona/gladius_argentum.png", False, True,  "Silver sword of merit."),
    ("gladius_aes",      "Gladius Aes (Bronze)",     "dona", 3, 35, 0, 0, "dona/gladius_aes.png",      False, True,  "Bronze sword of merit."),
    ("corona_aurea",     "Corona Aurea",             "dona", 4, 40, 0, 0, "dona/corona_aurea.png",     False, True,  "Distinguished service crown."),
    ("corona_civica",    "Corona Civica",            "dona", 5, 30, 0, 0, "dona/corona_civica.png",    False, True,  "Awarded for saving/protecting a fellow member."),
    ("phalerae",         "Phalerae",                 "dona", 6, 25, 0, 0, "dona/phalerae.png",         False, True,  "Valor decoration."),
]

# Rack precedence order (top -> bottom) from spec Tier 2.
RACK = [
    ("cmd_commendation", "Commander's Commendation", "rack",  1, 20, 10, 0, "rack/cmd_commendation.png", False, True,  "CO discretionary commendation/coin."),
    ("meritorious",      "Meritorious Service",      "rack",  2, 18,  8, 0, "rack/meritorious.png",      False, True,  "Discretionary exceptional contribution."),
    ("real_world_deploy","Real World Deployment",    "rack",  3, 15,  5, 0, "rack/real_world_deploy.png",False, True,  "Real-world activation (Kerrville-type)."),
    ("founder",          "Founder (Conditor Legionis)","rack",4, 15,  0, 1, "rack/founder.png",          True,  True,  "One of the six founding members."),
    ("officer",          "Officer / Warrant",        "rack",  5, 12,  0, 1, "rack/officer.png",          True,  True,  "Commissioned or appointed."),
    ("nco",              "NCO",                      "rack",  6, 10,  0, 1, "rack/nco.png",              True,  True,  "Promoted to NCO (E-5+)."),
    ("leadership",       "Leadership (Billet)",      "rack",  7, 10,  5, 0, "rack/leadership.png",       True,  True,  "Held a shop lead/TL/ATL/1SG/command billet."),
    ("mission_leader",   "Mission Leader",           "rack",  8,  8,  3, 0, "rack/mission_leader.png",   False, True,  "Led a mission/lane at an FTX."),
    ("instructor_ftx",   "Instructor (FTX)",         "rack",  9,  8,  3, 0, "rack/instructor_ftx.png",   False, True,  "Taught an FTX class."),
    ("instructor_online","Instructor (Online)",      "rack", 10,  5,  2, 0, "rack/instructor_online.png",False, True,  "Taught a virtual class."),
    ("patched",          "Patched Member",           "rack", 11,  8,  0, 1, "rack/patched.png",          True,  True,  "Completed TRADOC and patched in."),
    ("tradoc",           "TRADOC Completion",        "rack", 12,  6,  0, 1, "rack/tradoc.png",           True,  True,  "Finished all four rotating TRADOC blocks."),
    ("qual_weapons",     "Qualification (Weapons)",  "rack", 13,  8,  0, 1, "rack/qual_weapons.png",     True,  True,  "Passed weapons qualification."),
    ("qual_landnav",     "Qualification (Land Nav)", "rack", 14,  5,  0, 1, "rack/qual_landnav.png",     True,  True,  "Supplemental land navigation qual."),
    ("qual_comms",       "Qualification (Comms)",    "rack", 15,  5,  0, 1, "rack/qual_comms.png",       True,  True,  "Supplemental communications qual."),
    ("qual_medical",     "Qualification (Medical)",  "rack", 16,  5,  0, 1, "rack/qual_medical.png",     True,  True,  "Supplemental medical qual."),
    ("ham",              "Amateur Radio",            "rack", 17,  5,  3, 3, "rack/ham.png",              True,  True,  "HAM license: Tech/General/Extra (devices per class)."),
    ("perfect_year",     "Perfect Year",             "rack", 18,  8,  4, 0, "rack/perfect_year.png",     True,  True,  "Attended every monthly FTX in a calendar year."),
    ("mcftx",            "MCFTX",                    "rack", 19,  6,  2, 0, "rack/mcftx.png",            True,  True,  "Attended a multi-company FTX."),
    ("ftx",              "FTX Attendance",           "rack", 20,  4,  2, 4, "rack/ftx.png",              True,  True,  "Base attendance; devices at 5/10/25/50."),
    ("recruiter",        "Recruiter",                "rack", 21,  6,  3, 3, "rack/recruiter.png",        False, True,  "Recruited a patched member; devices 3/5/10."),
    ("recruit",          "Recruit Service",          "rack", 22,  2,  0, 1, "rack/recruit.png",          True,  True,  "Joined the unit."),
    ("esprit",           "Esprit de Corps",          "rack", 23,  5,  2, 0, "rack/esprit.png",           False, True,  "Morale/community contribution."),
]

TABS = [
    ("equites",      "Equites",      "tab", 1, 15, 0, 0, "tabs/equites.png",      False, True, "Elite qualification tab."),
    ("sharpshooter", "Sharpshooter", "tab", 2, 13, 0, 0, "tabs/sharpshooter.png", False, True, "TSM-official; requires Marksman."),
    ("marksman",     "Marksman",     "tab", 3, 10, 0, 0, "tabs/marksman.png",     False, True, "TSM-official marksmanship tab."),
    ("sabre",        "Sabre",        "tab", 4,  8, 0, 0, "tabs/sabre.png",        False, True, "Sabre qualification tab."),
]

# Tenure discs — point values only; discs are computed from join_date at render.
TENURE = [
    ("anni_gold",   "Anni Stipendiorum — Gold (5 yr)",   "tenure", 1, 15, 0, 0, "discs/anni_gold.png",   True, False, "Five years of service."),
    ("anni_silver", "Anni Stipendiorum — Silver (3 yr)", "tenure", 2,  9, 0, 0, "discs/anni_silver.png", True, False, "Three years of service."),
    ("anni_bronze", "Anni Stipendiorum — Bronze (1 yr)", "tenure", 3,  3, 0, 0, "discs/anni_bronze.png", True, False, "One year of service."),
]

ALL_ROWS = DONA + RACK + TABS + TENURE


def upgrade():
    op.create_table(
        "ribbon_catalog",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(48), nullable=False),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("section", sa.String(8), nullable=False),
        sa.Column("precedence", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("base_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("device_increment", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_devices", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("image", sa.String(160), nullable=True),
        sa.Column("is_auto", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("claimable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_ribbon_catalog_code", "ribbon_catalog", ["code"], unique=True)

    op.create_table(
        "member_ribbons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("ribbon_code", sa.String(48), sa.ForeignKey("ribbon_catalog.code"), nullable=False),
        sa.Column("device_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("awarded_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("awarded_by", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(8), nullable=False, server_default="manual"),
        sa.UniqueConstraint("member_id", "ribbon_code", name="uq_member_ribbon"),
    )
    op.create_index("ix_member_ribbons_member_id", "member_ribbons", ["member_id"])
    op.create_index("ix_member_ribbons_ribbon_code", "member_ribbons", ["ribbon_code"])

    # Seed catalog
    cat = sa.table(
        "ribbon_catalog",
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("section", sa.String), sa.column("precedence", sa.Integer),
        sa.column("base_points", sa.Integer), sa.column("device_increment", sa.Integer),
        sa.column("max_devices", sa.Integer), sa.column("image", sa.String),
        sa.column("is_auto", sa.Boolean), sa.column("claimable", sa.Boolean),
        sa.column("description", sa.Text), sa.column("active", sa.Boolean),
    )
    op.bulk_insert(cat, [
        {
            "code": c, "name": n, "section": s, "precedence": p, "base_points": bp,
            "device_increment": di, "max_devices": md, "image": img,
            "is_auto": ia, "claimable": cl, "description": d, "active": True,
        }
        for (c, n, s, p, bp, di, md, img, ia, cl, d) in ALL_ROWS
    ])


def downgrade():
    op.drop_index("ix_member_ribbons_ribbon_code", table_name="member_ribbons")
    op.drop_index("ix_member_ribbons_member_id", table_name="member_ribbons")
    op.drop_table("member_ribbons")
    op.drop_index("ix_ribbon_catalog_code", table_name="ribbon_catalog")
    op.drop_table("ribbon_catalog")
