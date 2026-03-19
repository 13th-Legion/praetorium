"""Rank history model — tracks all promotions/demotions for audit trail."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RankHistory(Base):
    """A record of a rank change for a member."""

    __tablename__ = "rank_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer, ForeignKey("members.id"), index=True)
    old_rank: Mapped[Optional[str]] = mapped_column(String(4))   # null if first assignment
    new_rank: Mapped[str] = mapped_column(String(4))
    changed_by: Mapped[Optional[str]] = mapped_column(String(64))  # NC username of who made the change
    notes: Mapped[Optional[str]] = mapped_column(Text)
    effective_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<RankHistory member={self.member_id} {self.old_rank}→{self.new_rank} @ {self.effective_date}>"
