"""Deck cards already filed from the Praetorium feedback form."""

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FeedbackDeckCard(Base):
    __tablename__ = "feedback_deck_cards"

    submission_id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
