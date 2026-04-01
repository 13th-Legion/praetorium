"""Shared constants — ranks, roles, teams, and form options.

Single source of truth. Import from here, not from route files.
"""

# ─── Rank Abbreviations ──────────────────────────────────────────────────────

RANK_ABBR: dict[str, str] = {
    "E-1": "RCT", "E-2": "PV2", "E-3": "PFC", "E-4": "CPL",
    "E-5": "SGT", "E-6": "SSG", "E-7": "SFC", "E-8": "1SG",
    "E-9": "SGM", "W-1": "WO1", "W-2": "CW2",
    "O-1": "2LT", "O-2": "1LT", "O-3": "CPT", "O-4": "MAJ",
}

RANK_TITLE: dict[str, str] = {
    "E-1": "Recruit", "E-2": "Private", "E-3": "Private First Class",
    "E-4": "Corporal", "E-5": "Sergeant", "E-6": "Staff Sergeant",
    "E-7": "Sergeant First Class", "E-8": "First Sergeant",
    "E-9": "Sergeant Major", "W-1": "Warrant Officer 1",
    "W-2": "Chief Warrant Officer 2",
    "O-1": "Second Lieutenant", "O-2": "First Lieutenant",
    "O-3": "Captain", "O-4": "Major",
}

# Dropdown choices for member edit forms: (grade, "ABBR — Title")
RANK_CHOICES: list[tuple[str, str]] = [
    (grade, f"{RANK_ABBR[grade]} — {RANK_TITLE[grade]}")
    for grade in RANK_ABBR
]

# ─── Authorization Role Sets ─────────────────────────────────────────────────

COMMAND_ROLES: set[str] = {"command", "admin"}
S1_ROLES: set[str] = {"command", "admin", "s1_lead"}
PIPELINE_ROLES: set[str] = {"command", "admin", "s1", "s1_lead"}
AWARD_ROLES: set[str] = {"command", "admin", "s1", "leader"}

# ─── Team / Element Constants ────────────────────────────────────────────────

TEAM_ORDER: dict[str, int] = {
    "Headquarters": 0, "Alpha": 1, "Bravo": 2,
    "Charlie": 3, "Delta": 4, "Echo": 5,
    "Foxtrot": 6,
}

TEAM_OPTIONS: list[str] = list(TEAM_ORDER.keys())

# Geographic zone assignment: 6 equal 60° slices from center point
# Center: I-30 & N Great Southwest Pkwy (32.7512, -97.0457)
GEO_CENTER = (32.7512, -97.0457)
GEO_ZONE_START = 330  # Alpha starts at 330°
GEO_ZONE_SIZE = 60
GEO_ZONE_TEAMS = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]

# Team designation letter → default name (for rename validation)
TEAM_DESIGNATION = {
    "Alpha": "A", "Bravo": "B", "Charlie": "C",
    "Delta": "D", "Echo": "E", "Foxtrot": "F",
}

# NC Talk room tokens for team channels
TEAM_TALK_TOKENS = {
    "Alpha": "rjdwjoaq", "Bravo": "dazi89uv", "Charlie": "z99wo7e4",
    "Delta": "zzw2m7gq", "Echo": "s6qbnaae", "Foxtrot": "ftkdo954",
    "Headquarters": "ogeyhrzd",
}

LEADERSHIP_TITLES = [
    "Commanding Officer",
    "Executive Officer",
    "First Sergeant",
    "Platoon Leader",
    "Platoon Sergeant",
    "Squad Leader",
    "Team Leader",
    "Assistant Team Leader",
]

STATUS_OPTIONS: list[str] = ["recruit", "active", "separated", "inactive", "blacklisted"]
