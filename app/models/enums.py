"""Enumeration types for the data model."""

import enum


class RankGrade(str, enum.Enum):
    """13th Legion rank grades (Roman military tradition)."""
    # Enlisted
    E1 = "E-1"   # Tiro (Recruit)
    E2 = "E-2"   # Miles
    E3 = "E-3"   # Miles Gregarius
    E4 = "E-4"   # Immune
    E5 = "E-5"   # Decanus
    E6 = "E-6"   # Tesserarius
    E7 = "E-7"   # Signifier
    E8 = "E-8"   # Optio
    E9 = "E-9"   # Primus Pilus
    # Officers
    O1 = "O-1"   # Vexillarian
    O2 = "O-2"   # Tribune
    O3 = "O-3"   # Centurion
    O4 = "O-4"   # Praefect
    # Warrant
    W1 = "W-1"   # Warrant Officer


class MemberStatus(str, enum.Enum):
    ACTIVE = "active"
    RECRUIT = "recruit"
    INACTIVE = "inactive"
    SEPARATED = "separated"
    BLACKLISTED = "blacklisted"


class TrainingBlock(str, enum.Enum):
    BLOCK1 = "Theory / Medical"
    BLOCK2 = "Weapons Qual"
    BLOCK3 = "Supplemental (Comms / Land Nav)"
    BLOCK4 = "Combat Fundamentals"


class TeamAssignment(str, enum.Enum):
    HQ = "Headquarters"
    ARROW = "Arrow"
    BADGER = "Badger"
    CHAOS = "Chaos"
    DELTA = "Delta"
