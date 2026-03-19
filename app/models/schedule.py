"""Event schedule & mission planning models — PP-070 / S3 Dashboard."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String, Text, DateTime, Integer, Time,
    ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EventScheduleBlock(Base):
    """A single time block in an event's training schedule."""

    __tablename__ = "event_schedule_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))

    # Schedule placement
    day_number: Mapped[int] = mapped_column(Integer, default=1)  # 1-based day of event
    start_time: Mapped[str] = mapped_column(String(4))  # Military time: "0800"
    end_time: Mapped[Optional[str]] = mapped_column(String(4))  # Military time: "0900", nullable

    # Content
    title: Mapped[str] = mapped_column(String(128))  # e.g., "CLASS: Individual Movement Techniques"
    activity_type: Mapped[str] = mapped_column(String(16), default="class")
    # Types: class, mission, formation, meal, admin, break, other
    instructor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Sort
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # Audit
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ScheduleBlock event={self.event_id} day={self.day_number} {self.start_time} {self.title}>"
