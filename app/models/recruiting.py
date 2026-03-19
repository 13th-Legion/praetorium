"""Recruiting models — recruiter roster, document signatures, separation log."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Boolean, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Recruiter(Base):
    """Active recruiters for auto-assignment load balancing."""

    __tablename__ = "recruiters"

    id: Mapped[int] = mapped_column(primary_key=True)
    nc_username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    current_load: Mapped[int] = mapped_column(Integer, default=0)
    max_load: Mapped[int] = mapped_column(Integer, default=5)
    total_recruited: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DocumentSignature(Base):
    """Digital signature records for NDAs and waivers."""

    __tablename__ = "document_signatures"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    document_type: Mapped[str] = mapped_column(String(32))  # 'nda', 'general_waiver'
    document_version: Mapped[str] = mapped_column(String(16), default="1.0")
    full_name: Mapped[str] = mapped_column(String(128))
    signature_text: Mapped[str] = mapped_column(String(256))  # typed "I agree" signature
    signed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    user_agent: Mapped[Optional[str]] = mapped_column(Text)


class SeparationLog(Base):
    """Offboarding/separation audit trail."""

    __tablename__ = "separation_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    reason: Mapped[str] = mapped_column(String(64))  # voluntary, involuntary, inactivity, blacklisted
    initiated_by: Mapped[str] = mapped_column(String(64))  # NC username of admin
    notes: Mapped[Optional[str]] = mapped_column(Text)
    nc_account_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    portal_access_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    groups_removed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
