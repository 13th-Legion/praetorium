"""app_settings — generic typed key/value store for scalar config dials.

Replaces scattered hardcoded scalars that a unit admin would tune without a
code change (audit db-config #10/#11/#13):
  - geo center lat/lng, zone start/size (constants.GEO_*)
  - WARNO/announcements Talk room token (events.WARNO_TALK_ROOM)
  - tenure ribbon point values (ribbon_points.TENURE_POINTS)
  - FTX device thresholds (ribbon_derive.FTX_DEVICE_THRESHOLDS)
  - MAX_SHOPS, session_max_age, etc.

Values are stored as strings with a `value_type` hint (str/int/float/bool/json)
so the service can coerce them back. The service exposes typed getters with a
default, and always falls back to the provided default if the key is missing —
so nothing breaks pre-seed.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # str | int | float | bool | json
    value_type: Mapped[str] = mapped_column(String(8), nullable=False, default="str")
    # Human label / category for the AdminCP UI.
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
