"""nc_rooms — DB-backed Nextcloud Talk channel directory (SSoT).

Curated list of NC Talk rooms usable as online-meeting locations. Powers the
"online meeting" channel dropdown on the events form and lets us build the
join link (https://<nc>/call/<token>) automatically.

Also the durable home for the named unit rooms (Announcements/WARNO) that were
previously hardcoded tokens. AdminCP-editable (mirrors the P1 config pattern).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NCRoom(Base):
    __tablename__ = "nc_rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NC Talk conversation token (stable id used in URLs + API). Unique.
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Display name shown in the dropdown, e.g. "T1 · Command".
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    # Whether this room may be picked as an online-meeting location.
    meeting_selectable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=99)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
