"""Member status history model — audit trail for status transitions."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemberStatusHistory(Base):
    """A record of a status change for a member."""

    __tablename__ = "member_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer, ForeignKey("members.id"), index=True)
    old_status: Mapped[Optional[str]] = mapped_column(String(16))
    new_status: Mapped[str] = mapped_column(String(16))
    changed_by: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return (
            f"<MemberStatusHistory member={self.member_id} "
            f"{self.old_status}->{self.new_status} by {self.changed_by}>"
        )
