"""Database models for the S6 Comms dashboard (PP-383).

Feature areas:
  * EmComm Plan     — single editable document (draft / published)
  * Channel ref     — channels + frequencies (seedable from the channel-plan CSV)
  * Net schedule    — recurring radio nets
  * Radio inventory — radios/equipment with checkout
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class S6EmcommPlan(Base):
    """Single-row editable Emergency Comms Plan."""
    __tablename__ = "s6_emcomm_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    content: Mapped[str] = mapped_column(Text, default="")
    draft_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class S6Channel(Base):
    """Channel / frequency reference row."""
    __tablename__ = "s6_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    channel_no: Mapped[str] = mapped_column(String(16), default="")
    channel_type: Mapped[str] = mapped_column(String(32), default="")
    alias: Mapped[str] = mapped_column(String(64), default="")
    rx_freq: Mapped[str] = mapped_column(String(16), default="")
    tx_freq: Mapped[str] = mapped_column(String(16), default="")
    tx_power: Mapped[str] = mapped_column(String(16), default="")
    dec_tone: Mapped[str] = mapped_column(String(32), default="")
    enc_tone: Mapped[str] = mapped_column(String(32), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class S6NetSchedule(Base):
    """Recurring radio net entry."""
    __tablename__ = "s6_net_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_of_week: Mapped[str] = mapped_column(String(16), default="")
    start_time: Mapped[str] = mapped_column(String(16), default="")
    frequency: Mapped[str] = mapped_column(String(32), default="")
    alias: Mapped[str] = mapped_column(String(64), default="")
    purpose: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class S6Radio(Base):
    """Radio / equipment inventory item with checkout."""
    __tablename__ = "s6_radios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    radio_type: Mapped[str] = mapped_column(String(64), default="")
    serial: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="available")  # available | checked_out | maintenance
    checked_out_to: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    checked_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
