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
    # 255, not 45: prod stores IPv6 + Cloudflare-forwarded chains
    # (e.g. "2600:1700:...:27e5, 198.41.227.38"), up to ~56 chars observed.
    ip_address: Mapped[Optional[str]] = mapped_column(String(255))
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
    # Talk eviction and device-token revocation are NOT things a disabled NC
    # account gives you for free -- see the notes on process_offboarding().
    # They are performed by the host-side offboard-reconcile timer (the portal
    # container has no docker socket and cannot run occ), so these start False
    # and are flipped when that reconciler confirms them.
    talk_removed: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Steps that must be true before a separation is mechanically complete.
    CLEANUP_STEPS = (
        ("nc_account_disabled", "NC account disabled"),
        ("groups_removed", "NC groups removed"),
        ("talk_removed", "Talk rooms evicted"),
        ("tokens_revoked", "Device tokens revoked"),
    )

    @property
    def cleanup_outstanding(self) -> list[str]:
        """Human labels for cleanup steps that have NOT been confirmed."""
        return [label for attr, label in self.CLEANUP_STEPS if not getattr(self, attr, False)]

    @property
    def cleanup_complete(self) -> bool:
        return not self.cleanup_outstanding
