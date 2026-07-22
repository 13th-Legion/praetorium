"""event_categories — DB-backed event taxonomy (single source of truth).

Replaces the per-category dicts re-declared across events.py (VALID_CATEGORIES,
CATEGORY_LABELS, CATEGORY_ICONS, RSVP_CATEGORIES, WARNO_LEAD_DAYS). Adding a
category ("range_day") or changing an icon/label/RSVP-default/WARNO lead was a
4-dict edit; now one row.

(FILTER_TABS groupings and TITLE_CATEGORY_MAP keyword rules remain in code for
now — they're presentation/keyword logic, not the drift-prone per-category
fields. Candidates for a later pass / AdminCP.)
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EventCategory(Base):
    __tablename__ = "event_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(48), nullable=False)
    icon: Mapped[str] = mapped_column(String(16), nullable=False, default="📅")
    # Whether this category gets RSVP by default.
    rsvp_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # WARNO auto-schedule lead days; NULL = no auto-WARNO for this category.
    warno_lead_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=99)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
