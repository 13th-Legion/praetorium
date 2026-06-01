"""Weapons Qualification — per-member pass/fail recorded against an FTX event."""

from datetime import datetime, date

from sqlalchemy import String, Integer, Boolean, DateTime, Date, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemberWeaponsQual(Base):
    """A weapons-qualification result for a member, tied to a specific FTX event."""

    __tablename__ = "member_weapons_qual"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer, index=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)  # the FTX event
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    qualified_on: Mapped[date | None] = mapped_column(Date, nullable=True)  # event date_start
    recorded_by: Mapped[str] = mapped_column(String(64))  # nc_username
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self):
        return f"<MemberWeaponsQual m{self.member_id} e{self.event_id} {'PASS' if self.passed else 'FAIL'}>"
