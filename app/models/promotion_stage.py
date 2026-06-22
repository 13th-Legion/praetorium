"""Promotion staging model — staged rank changes gameplanned for an FTX/formation.

Promotions & patchings are planned the week of an FTX and performed in formation.
If an intended member doesn't show, they don't get promoted/patched. Staged rows
let S1/Command plan the changes, prune no-shows after the FTX, then finalize
(which executes the real rank change).
"""

from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Text, Date, DateTime, Boolean, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Officer pay grades — to_rank in these is an officer commission/patching.
OFFICER_GRADES = {"O-1", "O-2", "O-3", "O-4"}


class PromotionStage(Base):
    """A staged (planned-but-not-yet-executed) rank change for a member."""

    __tablename__ = "promotion_stages"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer, ForeignKey("members.id"), index=True)
    from_rank: Mapped[Optional[str]] = mapped_column(String(4))   # member's current rank at staging
    to_rank: Mapped[str] = mapped_column(String(4))               # the planned new rank
    action_type: Mapped[str] = mapped_column(String(16), default="promotion")  # 'promotion'|'patch'|'demotion'
    is_officer: Mapped[bool] = mapped_column(Boolean, default=False)  # True if to_rank in O-1..O-4
    status: Mapped[str] = mapped_column(String(16), default="staged")  # 'staged'|'finalized'|'cancelled'
    target_date: Mapped[Optional[date]] = mapped_column(Date)      # planned FTX/formation date
    notes: Mapped[Optional[str]] = mapped_column(Text)
    staged_by: Mapped[Optional[str]] = mapped_column(String(64))
    staged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finalized_by: Mapped[Optional[str]] = mapped_column(String(64))
    finalized_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    def __repr__(self):
        return (
            f"<PromotionStage member={self.member_id} "
            f"{self.from_rank}→{self.to_rank} {self.action_type} [{self.status}]>"
        )
