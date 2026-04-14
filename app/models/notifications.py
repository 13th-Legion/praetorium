from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index
from app.database import Base

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False)
    category = Column(String(32), nullable=False)
    title = Column(String(256), nullable=False)
    body = Column(Text, nullable=True)
    link = Column(String(512), nullable=True)
    icon = Column(String(8), nullable=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_notifications_member_read", "member_id", "read_at"),
    )
