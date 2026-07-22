"""Shared constants — ranks, roles, teams, and form options.

Single source of truth. Import from here, not from route files.
"""

# ─── Rank Abbreviations ──────────────────────────────────────────────────────

RANK_ABBR: dict[str, str] = {
    "E-1": "RCT", "E-2": "PV2", "E-3": "PFC", "E-4": "CPL",
    "E-5": "SGT", "E-6": "SSG", "E-7": "SFC", "E-8M": "MSG", "E-8": "1SG",
    "E-9": "SGM", "W-1": "WO1", "W-2": "CW2", "W-3": "CW3", "W-4": "CW4", "W-5": "CW5",
    "O-1": "2LT", "O-2": "1LT", "O-3": "CPT", "O-4": "MAJ",
}

RANK_TITLE: dict[str, str] = {
    "E-1": "Recruit", "E-2": "Private", "E-3": "Private First Class",
    "E-4": "Corporal", "E-5": "Sergeant", "E-6": "Staff Sergeant",
    "E-7": "Sergeant First Class", "E-8M": "Master Sergeant", "E-8": "First Sergeant",
    "E-9": "Sergeant Major", "W-1": "Warrant Officer 1",
    "W-2": "Chief Warrant Officer 2",
    "W-3": "Chief Warrant Officer 3", "W-4": "Chief Warrant Officer 4",
    "W-5": "Chief Warrant Officer 5",
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
# Unit Comms + Legionary Dispatch newsletter — open to all of S1, not just the
# S1 lead (Cav directive 2026-06-25). Does NOT grant other S1 admin actions.
UNIT_COMMS_ROLES: set[str] = {"command", "admin", "s1", "s1_lead"}
AWARD_ROLES: set[str] = {"command", "admin", "s1", "leader"}

# ─── Team / Element Constants ────────────────────────────────────────────────

# NOTE: "Aquila" is the North zone (formerly "Alpha", renamed 2026-07-21). The
# geo/zone math + designation letter treat it as the 'A' slot. The team-rename
# feature mutates these dicts at runtime; the values here are the persisted
# defaults so a rename survives app restarts/redeploys. If a team is renamed
# again, update these defaults too (or move teams to a DB table — see
# ADMINCP_SPEC single-source-of-truth audit).
TEAM_ORDER: dict[str, int] = {
    "Headquarters": 0, "Aquila": 1, "Bravo": 2,
    "Charlie": 3, "Delta": 4, "Echo": 5,
    "Foxtrot": 6,
}

TEAM_OPTIONS: list[str] = list(TEAM_ORDER.keys())

# Geographic zone assignment: 6 equal 60° slices from center point
# Center: I-30 & N Great Southwest Pkwy (32.7512, -97.0457)
GEO_CENTER = (32.7512, -97.0457)
GEO_ZONE_START = 330  # Aquila (North) starts at 330°
GEO_ZONE_SIZE = 60
GEO_ZONE_TEAMS = ["Aquila", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]

# Team designation letter → default name (for rename validation)
TEAM_DESIGNATION = {
    "Aquila": "A", "Bravo": "B", "Charlie": "C",
    "Delta": "D", "Echo": "E", "Foxtrot": "F",
}

# NC Talk room tokens for team channels
TEAM_TALK_TOKENS = {
    "Aquila": "rjdwjoaq", "Bravo": "dazi89uv", "Charlie": "z99wo7e4",
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


# ─── Recipient Groups (shared between Email Blast and Events) ────────────────

# Matcher types understood by _resolve_invite_groups:
#   filter:      Member.status in [...]
#   roles:       any of Member.portal_roles in [...]
#   leadership:  Member.leadership_title in [...]
#   billet_lead: Member.primary_billet contains "(Lead)"  (shop heads)
#   team:        Member.team == <team name>   (added dynamically for each team)
# Order here is the order rendered in the invite-groups checkbox grid. Team
# groups (Aquila..Foxtrot) are injected after "Shop Heads" by
# build_recipient_groups() so they track the DB teams table.
RECIPIENT_GROUPS: dict[str, dict] = {
    "entire_unit": {"label": "13th Legion", "filter": ["active", "recruit"]},
    "patched": {"label": "Patched", "filter": ["active"]},
    "recruits": {"label": "Recruits", "filter": ["recruit"]},
    "leaders": {"label": "Leaders", "roles": ["command", "leader", "officer", "nco"]},
    "officers": {"label": "Officers", "roles": ["command", "officer"]},
    "ncos": {"label": "NCOs", "roles": ["nco"]},
    "team_leaders": {"label": "Team Leaders", "leadership": ["Team Leader", "Assistant Team Leader"]},
    "shop_heads": {"label": "Shop Heads", "billet_lead": True},
    # Team groups injected here dynamically (Team Aquila .. Team Foxtrot)
    "s1": {"label": "S1 — Administration", "roles": ["s1", "s1_lead"]},
    "s2": {"label": "S2 — Intelligence & Security", "roles": ["s2"]},
    "s3": {"label": "S3 — Training & Operations", "roles": ["s3"]},
    "s4": {"label": "S4 — Logistics", "roles": ["s4"]},
    "s5": {"label": "S5 — Medical", "roles": ["s5"]},
    "s6": {"label": "S6 — Communications", "roles": ["s6"]},
    "command": {"label": "Command", "roles": ["command"]},
}
