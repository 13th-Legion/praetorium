"""Training models — TRADOC checklist, certifications, training claims."""

from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    String, Text, Date, DateTime, Boolean, Integer,
    ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TradocItem(Base):
    """A single TRADOC checklist item (e.g., 'Basic Rifle Marksmanship')."""

    __tablename__ = "tradoc_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    block: Mapped[int] = mapped_column(Integer)  # 1-4, 0 = every FTX
    block_name: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    signoffs: Mapped[list["MemberTradoc"]] = relationship(back_populates="item")

    def __repr__(self):
        return f"<TradocItem BLK{self.block}: {self.name}>"


class MemberTradoc(Base):
    """Sign-off record: a member completed a TRADOC item."""

    __tablename__ = "member_tradoc"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("tradoc_items.id"))
    signed_off_by: Mapped[Optional[str]] = mapped_column(String(64))  # NC username of approver
    signed_off_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ftx_date: Mapped[Optional[date]] = mapped_column(Date)  # Which FTX this was signed off at
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Relationships
    item: Mapped["TradocItem"] = relationship(back_populates="signoffs")

    def __repr__(self):
        return f"<MemberTradoc member={self.member_id} item={self.item_id}>"


class Certification(Base):
    """A certification or tab that can be earned (e.g., 'Sharpshooter')."""

    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    category: Mapped[str] = mapped_column(String(32))  # 'marksmanship', 'sar', 'leadership', 'specialty'
    description: Mapped[Optional[str]] = mapped_column(Text)
    criteria: Mapped[Optional[str]] = mapped_column(Text)
    resources: Mapped[Optional[str]] = mapped_column(Text)
    icon: Mapped[Optional[str]] = mapped_column(String(8))  # emoji
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    awards: Mapped[list["MemberCertification"]] = relationship(back_populates="certification")

    def __repr__(self):
        return f"<Certification {self.name}>"


class MemberCertification(Base):
    """Record of a member earning a certification/tab."""

    __tablename__ = "member_certifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    certification_id: Mapped[int] = mapped_column(ForeignKey("certifications.id"))
    awarded_by: Mapped[Optional[str]] = mapped_column(String(64))  # NC username
    awarded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    doc_path: Mapped[Optional[str]] = mapped_column(Text)  # path to supporting doc

    # Relationships
    certification: Mapped["Certification"] = relationship(back_populates="awards")

    def __repr__(self):
        return f"<MemberCert member={self.member_id} cert={self.certification_id}>"


class TrainingClaim(Base):
    """Member-submitted training claim pending review."""

    __tablename__ = "training_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    claim_type: Mapped[str] = mapped_column(String(16))  # 'tradoc' or 'certification'
    reference_id: Mapped[Optional[int]] = mapped_column(Integer)  # tradoc_item.id or certification.id
    description: Mapped[Optional[str]] = mapped_column(Text)
    doc_path: Mapped[Optional[str]] = mapped_column(Text)  # uploaded file path
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending, approved, denied
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    review_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<TrainingClaim member={self.member_id} type={self.claim_type} status={self.status}>"
