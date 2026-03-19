"""Awards model — Gladius and other unit awards."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemberAward(Base):
    """A unit award (e.g., Gladius) bestowed on a member."""

    __tablename__ = "member_awards"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    award_name: Mapped[str] = mapped_column(String(64), default="Gladius")
    reason: Mapped[Optional[str]] = mapped_column(Text)
    awarded_by: Mapped[Optional[str]] = mapped_column(String(64))  # NC username
    awarded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self):
        return f"<MemberAward member={self.member_id} award={self.award_name}>"
