"""Rank model — DB-backed rank structure (single source of truth).

Replaces the hardcoded rank dicts that had drifted across 5+ files:
  - constants.RANK_ABBR / RANK_TITLE / RANK_CHOICES
  - promotions.RANK_ORDER / RANK_INDEX (the most complete ordering)
  - chain_of_command.RANK_INSIGNIA
  - member_edit.RANK_GROUPS (grade -> NC group; was missing E-8M, W-2..W-5)
  - elections.ELIGIBLE_RANK_GRADES (listed nonexistent O-5/O-6/W-5)
  - roster.RANK_ORDER / attendance_analytics.RANK_ABBR (was keyed 'e4' -> blank)

Keyed by `grade` (the stable code stored on Member.rank_grade, e.g. "E-4").
Adding a grade, fixing an abbreviation, or changing election eligibility is now
one row edit (soon via AdminCP) instead of a 5-file code change.

`pay_category` groups grades (enlisted/nco/warrant/officer) for the AdminCP
rank-axis permission matrix and for rank-group logic.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Rank(Base):
    __tablename__ = "ranks"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable code stored on Member.rank_grade, e.g. "E-4", "E-8M", "O-3". Unique.
    grade: Mapped[str] = mapped_column(String(4), unique=True, nullable=False)
    abbr: Mapped[str] = mapped_column(String(8), nullable=False)          # "CPL"
    title: Mapped[str] = mapped_column(String(48), nullable=False)        # "Corporal"
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=99)
    # SVG insignia filename (chain-of-command display); NULL for grades w/o art.
    insignia: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # NC group this grade maps to ("Rank - NCO", etc.) for group sync.
    nc_group: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    # enlisted | nco | warrant | officer — rank grouping for policy/permissions.
    pay_category: Mapped[str] = mapped_column(String(16), nullable=False, default="enlisted")
    # Whether members of this grade may stand for election (CO/leadership).
    election_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
