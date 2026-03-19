"""Threat Level Assignment System (TLAS) model."""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Integer, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ThreatLevel(str, enum.Enum):
    GREEN = "green"
    BLUE = "blue"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"
    BLACK = "black"


# Static TLAS data per the TSM doc
TLAS_CONFIG = {
    ThreatLevel.GREEN: {
        "label": "Low Condition",
        "risk": "Low risk of emergency / disaster",
        "color_hex": "#2e7d32",
        "color_bg": "#e8f5e9",
        "supply_kit": "3-day",
        "checkin": "Monday before 2100 hrs",
        "measures": [
            "Develop household emergency / disaster plan and assemble a disaster supply (3-day) kit.",
            "Develop geographical fire team (platoon) emergency / disaster plan.",
            "Assemble a year's supply of water, food, sanitary needs, medical needs, fuel, and defense tools.",
            "Train in first-aid, self-defense, and regularly attend TSM training events.",
        ],
    },
    ThreatLevel.BLUE: {
        "label": "Guarded Condition",
        "risk": "General risk of emergency / disaster",
        "color_hex": "#1565c0",
        "color_bg": "#e3f2fd",
        "supply_kit": "6-day",
        "checkin": "Monday / Thursday before 2100 hrs",
        "measures": [
            "Update and double disaster supply (6-day) kit.",
            "Review household and geographical fire team emergency / disaster plans.",
            "Hold a household / team meeting to discuss communication in the event of an incident.",
            "Develop a more detailed household / team / platoon communication plan.",
            "Members with special needs should discuss concerns with families and Team Leader.",
        ],
    },
    ThreatLevel.YELLOW: {
        "label": "Elevated Condition",
        "risk": "Significant risk of emergency / disaster",
        "color_hex": "#f9a825",
        "color_bg": "#fffde7",
        "supply_kit": "12-day",
        "checkin": "Monday / Wednesday / Saturday before 2100 hrs",
        "measures": [
            "Update and double disaster supply (12-day) kit.",
            "Review and update household / team / platoon emergency / disaster plan.",
            "Hold a household / team / platoon meeting and review communication plans.",
            "Be observant of suspicious activity and communicate it to your Fire Team Lead or higher.",
            "Review your evacuation plan and shelter-in-place plan.",
        ],
    },
    ThreatLevel.ORANGE: {
        "label": "High Condition",
        "risk": "High risk of emergency / disaster",
        "color_hex": "#e65100",
        "color_bg": "#fff3e0",
        "supply_kit": "12-day (cumulative)",
        "checkin": "Monday / Wednesday / Friday / Sunday before 2100 hrs",
        "measures": [
            "Review protective measures (evacuation and sheltering) for potential threats.",
            "Avoid high profile or symbolic locations.",
            "Exercise caution when traveling.",
        ],
    },
    ThreatLevel.RED: {
        "label": "Severe Condition",
        "risk": "Imminent threat of emergency / disaster",
        "color_hex": "#b71c1c",
        "color_bg": "#ffebee",
        "supply_kit": "12-day (cumulative)",
        "checkin": "Daily before 2100 hrs",
        "measures": [
            "Avoid public gathering places (sports arenas, airports, government buildings).",
            "Limit time near potential high value targets (nuclear plant, military base, power plant).",
            "Follow official instructions about restrictions to normal activities.",
            "Monitor TSM communication platforms for advisories or warnings.",
            "Prepare to take protective actions (sheltering or evacuation) when instructed.",
            "Deploy for mutual aid and assistance within your company or to another, as required.",
        ],
    },
    ThreatLevel.BLACK: {
        "label": "SITUATION IN-PROGRESS",
        "risk": "Complete domestic breakdown",
        "color_hex": "#000000",
        "color_bg": "#212121",
        "supply_kit": "N/A — Bug Out",
        "checkin": "Emergency comms — FRS / CB / HAM Radio",
        "measures": [
            "Proceed by the most expedient route to your designated Rally Point (RP).",
            "Set up security as inconspicuously as possible at RP.",
            "Bug Out with family to your designated Rally Point.",
            "Utilize FRS, CB, and/or HAM Radio designated emergency channels per company SOP.",
            "Monitor designated frequencies for instructions and check-in with Fire Team Leaders.",
            "FTLs elevate condition and observations throughout the chain of command.",
        ],
    },
}


class ThreatLevelEntry(Base):
    """Records each TLAS level change. Latest row = current level."""

    __tablename__ = "threat_levels"

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default=ThreatLevel.GREEN.value)
    set_by: Mapped[str] = mapped_column(String(64), nullable=False)  # NC username
    set_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
