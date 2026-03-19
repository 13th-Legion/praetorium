"""Member model — core entity for the portal."""

from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Text, Date, DateTime, Boolean, Enum, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.enums import RankGrade, MemberStatus, TeamAssignment


class Member(Base):
    """A 13th Legion (or TSM) member."""

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identity
    nc_username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(64))
    last_name: Mapped[str] = mapped_column(String(64))
    callsign: Mapped[Optional[str]] = mapped_column(String(32))
    serial_number: Mapped[Optional[str]] = mapped_column(String(20))
    serial_seq: Mapped[Optional[int]] = mapped_column(Integer)
    email: Mapped[Optional[str]] = mapped_column(String(128))

    # Assignment
    rank_grade: Mapped[Optional[str]] = mapped_column(String(4))  # E-1 through O-4, W-1
    status: Mapped[str] = mapped_column(String(16), default=MemberStatus.RECRUIT)
    team: Mapped[Optional[str]] = mapped_column(String(32))
    company: Mapped[str] = mapped_column(String(64), default="13th Legion")

    # Position & Billets
    leadership_title: Mapped[Optional[str]] = mapped_column(Text)  # CO, XO, PSG, TL, ATL
    primary_billet: Mapped[Optional[str]] = mapped_column(Text)    # Shop assignments
    secondary_billet: Mapped[Optional[str]] = mapped_column(String(64))

    # Service Record
    join_date: Mapped[Optional[date]] = mapped_column(Date)
    patch_date: Mapped[Optional[date]] = mapped_column(Date)
    separation_date: Mapped[Optional[date]] = mapped_column(Date)

    # Contact (PII — respect RBAC)
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    address: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(String(64))
    state: Mapped[Optional[str]] = mapped_column(String(2), default="TX")
    zip_code: Mapped[Optional[str]] = mapped_column(String(10))
    personal_email: Mapped[Optional[str]] = mapped_column(String(128))
    emergency_contact: Mapped[Optional[str]] = mapped_column(String(128))
    emergency_phone: Mapped[Optional[str]] = mapped_column(String(20))
    contact_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Radio
    ham_callsign: Mapped[Optional[str]] = mapped_column(String(10))
    ham_license_class: Mapped[Optional[str]] = mapped_column(String(16))
    gmrs_callsign: Mapped[Optional[str]] = mapped_column(String(10))

    # FTX tracking
    last_ftx: Mapped[Optional[date]] = mapped_column(Date)
    ftx_count: Mapped[int] = mapped_column(Integer, default=0)
    is_founder: Mapped[bool] = mapped_column(Boolean, default=False)

    # Organizational flags
    is_hq: Mapped[bool] = mapped_column(Boolean, default=False)  # HQ element (dual-assigned)

    # Flags
    is_veteran: Mapped[bool] = mapped_column(Boolean, default=False)
    mos: Mapped[Optional[str]] = mapped_column(Text)  # military MOS/branch if veteran
    has_ltc: Mapped[bool] = mapped_column(Boolean, default=False)

    # Payment tracking (PP-021)
    app_fee_status: Mapped[Optional[str]] = mapped_column(String(16), default="pending")  # pending, paid, waived
    app_fee_amount: Mapped[Optional[float]] = mapped_column(default=50.00)
    app_fee_paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    app_fee_method: Mapped[Optional[str]] = mapped_column(String(32))  # cash, venmo, zelle, paypal, waived

    # NDA / Waiver (PP-020)
    nda_signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    nda_ip_address: Mapped[Optional[str]] = mapped_column(String(255))
    waiver_signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    waiver_ip_address: Mapped[Optional[str]] = mapped_column(String(255))

    # Recruiter assignment (PP-022)
    assigned_recruiter: Mapped[Optional[str]] = mapped_column(String(64))  # NC username
    recruiter_assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Offboarding (PP-024)
    separation_reason: Mapped[Optional[str]] = mapped_column(String(64))
    separation_notes: Mapped[Optional[str]] = mapped_column(Text)
    separation_initiated_by: Mapped[Optional[str]] = mapped_column(String(64))

    # Conduct / Promotability
    non_promotable_until: Mapped[Optional[date]] = mapped_column(Date)
    non_promotable_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Portal metadata
    portal_roles: Mapped[Optional[str]] = mapped_column(Text)  # JSON list of roles from NC group sync
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Member {self.rank_grade or ''} {self.last_name} ({self.callsign or self.nc_username})>"

    @property
    def display_name(self) -> str:
        """e.g., '1LT Kavadas (Cav)'"""
        from app.constants import RANK_ABBR
        parts = []
        if self.rank_grade:
            parts.append(RANK_ABBR.get(self.rank_grade, self.rank_grade))
        parts.append(self.last_name)
        if self.callsign:
            parts.append(f"({self.callsign})")
        return " ".join(parts)
