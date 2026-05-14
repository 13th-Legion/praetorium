"""Code of Conduct violation history — permanent audit trail."""

from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Text, Date, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConductViolation(Base):
    """A recorded Code of Conduct violation for a member."""

    __tablename__ = "conduct_violations"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer, ForeignKey("members.id"), index=True)

    # What happened
    violation_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)  # description of the violation

    # Action taken
    action_taken: Mapped[str] = mapped_column(String(64))  # non-promotable, suspended, counseled, etc.
    duration_days: Mapped[Optional[int]] = mapped_column(Integer)  # null if indefinite or N/A
    start_date: Mapped[Optional[date]] = mapped_column(Date)  # when the action starts
    end_date: Mapped[Optional[date]] = mapped_column(Date)    # when it expires (null = indefinite)

    # Who imposed it
    issued_by: Mapped[str] = mapped_column(String(64))  # NC username
    notes: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ConductViolation member={self.member_id} action={self.action_taken} date={self.violation_date}>"
