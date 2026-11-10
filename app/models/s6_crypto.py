"""Models for the S6 Comms Crypto tools (shackle code, one-time pad, trench code)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class S6ShackleKey(Base):
    """A shackle key: a 10-letter isogram word + a 1-10 offset. Latest row is 'current'."""
    __tablename__ = "s6_shackle_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    word: Mapped[str] = mapped_column(String(16))
    offset: Mapped[int] = mapped_column(Integer)  # 1-10, 1-indexed start position
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class S6OtpPad(Base):
    """A one-time pad (set of letter pages)."""
    __tablename__ = "s6_otp_pads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    serial: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    burned: Mapped[bool] = mapped_column(Boolean, default=False)


class S6OtpPage(Base):
    """A single page of a one-time pad."""
    __tablename__ = "s6_otp_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pad_id: Mapped[int] = mapped_column(ForeignKey("s6_otp_pads.id"))
    page_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)  # space-separated 5-letter groups
    burned: Mapped[bool] = mapped_column(Boolean, default=False)


class S6TrenchCodebook(Base):
    """A trench codebook (phrase -> numeric code group)."""
    __tablename__ = "s6_trench_codebooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class S6TrenchEntry(Base):
    """One phrase -> code mapping inside a trench codebook."""
    __tablename__ = "s6_trench_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codebook_id: Mapped[int] = mapped_column(ForeignKey("s6_trench_codebooks.id"))
    phrase: Mapped[str] = mapped_column(String(128))
    code_group: Mapped[str] = mapped_column(String(8))
