"""member_statuses — DB-backed member status taxonomy (single source of truth).

Replaces constants.STATUS_OPTIONS + recruiting_analytics.STATUS_META /
LEFT_STATUSES / STAYED_STATUSES / IN_PROGRESS_STATUSES. Status drives filtering,
analytics buckets, and colors; the `lifecycle` flag classifies each status so
the analytics loops don't re-hardcode which codes count as "left"/"stayed"/
"in progress".
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemberStatus(Base):
    __tablename__ = "member_statuses"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#888888")
    # lifecycle bucket: in_progress | active | left
    lifecycle: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=99)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
