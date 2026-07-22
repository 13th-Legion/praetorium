"""Team / element model — DB-backed team metadata (single source of truth).

Replaces the hardcoded TEAM_ORDER / TEAM_DESIGNATION / TEAM_TALK_TOKENS /
GEO_ZONE_TEAMS dicts in app/constants.py. Those dicts only survived until a
restart because the team-rename feature mutated them in memory; a rename now
persists here instead.

`Member.team` still stores the team *name* string (the display name). This
table carries the metadata keyed by that name:
  - designation: the fixed letter for the geographic slot (A..F); HQ has none.
  - sort_order: roster ordering.
  - geo_zone_index: which 60° bearing slice this team owns (0..5), or NULL for
    non-geographic elements (Headquarters). Aquila(N)=0, Bravo=1, ... Foxtrot=5.
  - talk_token: NC Talk room token for the team channel.
  - color / emoji: map + roster display.
  - is_hq: organizational overlay element (Headquarters), not a geo team.
  - archived: soft-hide without deleting history.

Renaming a team = UPDATE this row's `name` + UPDATE Member.team for its
members. No code edit, survives restarts.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Display name — this is what Member.team stores. Unique.
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    # Fixed geo-slot designation letter (A..F). NULL for HQ / non-geo elements.
    designation: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=99)
    # 0-based bearing-slice index for geo assignment; NULL = not a geo team.
    geo_zone_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    talk_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    emoji: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    is_hq: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
