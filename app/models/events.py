"""Events & Attendance models — PP-060 / PP-070."""

from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    String, Text, Date, DateTime, Boolean, Integer,
    ForeignKey, Enum as SAEnum, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Event(Base):
    """A unit event (FTX, MCFTX, training course, social, etc.)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Core fields
    title: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32))
    # categories: ftx, mcftx, online_training, meeting, external_training, family_day, social, volunteering, other
    description: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(Text)
    instructor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)
    instructor: Mapped[Optional["Member"]] = relationship(foreign_keys=[instructor_id])

    # Schedule
    date_start: Mapped[datetime] = mapped_column(DateTime)
    date_end: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Status lifecycle: draft → warno_issued → opord_issued → active → complete | cancelled
    status: Mapped[str] = mapped_column(String(16), default="draft")

    # Training linkage
    training_block: Mapped[Optional[int]] = mapped_column(Integer)  # TRADOC block 1-4, null if N/A
    training_site: Mapped[Optional[str]] = mapped_column(String(16))  # able, baker, charlie, dog, easy
    rally_point: Mapped[Optional[str]] = mapped_column(Text)  # S2-assigned rally point address/description
    rally_point_set_by: Mapped[Optional[str]] = mapped_column(String(64))  # NC username
    rally_point_set_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # RSVP controls
    rsvp_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    rsvp_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # CalDAV sync (PP-070g)
    caldav_uid: Mapped[Optional[str]] = mapped_column(String(255), unique=True)

    # Document paths
    opord_path: Mapped[Optional[str]] = mapped_column(Text)

    # WARNO/OPORD/FRAGORD pipeline
    warno_scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # When WARNO auto-issues
    warno_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime)     # When WARNO actually issued
    opord_target_date: Mapped[Optional[datetime]] = mapped_column(DateTime)   # Expected OPORD date (for countdown)
    opord_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime)     # When OPORD published
    fragord_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime)   # When FRAGORD published

    # Finalization (PP-074)
    finalized_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finalized_by: Mapped[Optional[str]] = mapped_column(String(64))

    # Audit
    created_by: Mapped[str] = mapped_column(String(64))  # NC username
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    rsvps: Mapped[list["EventRSVP"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    documents: Mapped[list["EventDocument"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    guests: Mapped[list["EventGuest"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    buddy_pairs: Mapped[list["EventBuddyPair"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    guard_slots: Mapped[list["EventGuardSlot"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    guard_duties: Mapped[list["EventGuardDuty"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    vexillations: Mapped[list["EventVexillation"]] = relationship(back_populates="event", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Event {self.id}: {self.title} ({self.category}, {self.status})>"


class EventRSVP(Base):
    """RSVP and attendance record for a member at an event."""

    __tablename__ = "event_rsvps"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))

    # RSVP status: pending, attending, declined
    status: Mapped[str] = mapped_column(String(16), default="pending")
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    auto_declined: Mapped[bool] = mapped_column(Boolean, default=False)

    # Check-in (event day)
    checked_in: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_in_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    checked_in_by: Mapped[Optional[str]] = mapped_column(String(64))  # NC username of S1

    # Post-event confirmed attendance
    attended: Mapped[bool] = mapped_column(Boolean, default=False)

    # Audit
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    event: Mapped["Event"] = relationship(back_populates="rsvps")

    def __repr__(self):
        return f"<EventRSVP event={self.event_id} member={self.member_id} status={self.status}>"


class EventDocument(Base):
    """Document attached to an event (WARNO, OPORD, AAR, etc.)."""

    __tablename__ = "event_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))

    # Document metadata
    doc_type: Mapped[str] = mapped_column(String(16))
    # types: warno, opord, fragord, aar, annex, other
    title: Mapped[str] = mapped_column(String(128))
    file_path: Mapped[str] = mapped_column(Text)

    # Visibility: all_rsvp, attending_only, leadership, s3_only
    visibility: Mapped[str] = mapped_column(String(16), default="attending_only")

    # Audit
    uploaded_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    event: Mapped["Event"] = relationship(back_populates="documents")

    def __repr__(self):
        return f"<EventDocument {self.doc_type}: {self.title}>"


# ─── PP-070: Ops Console Models ───────────────────────────────────────────────


class EventGuest(Base):
    """Guest pre-registration (via RSVP) or day-of walk-in — PP-070f."""

    __tablename__ = "event_guests"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    rsvp_id: Mapped[Optional[int]] = mapped_column(ForeignKey("event_rsvps.id"), nullable=True)
    sponsor_id: Mapped[int] = mapped_column(ForeignKey("members.id"))

    first_name: Mapped[str] = mapped_column(String(64))
    last_name: Mapped[str] = mapped_column(String(64))

    # relation: spouse_partner, family, friend, prospect, inter_unit, other
    relation: Mapped[str] = mapped_column(String(16), default="other")

    notes: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    waiver_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    is_walkin: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_in_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    registered_by: Mapped[Optional[str]] = mapped_column(String(64))  # NC username

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    event: Mapped["Event"] = relationship(back_populates="guests")

    def __repr__(self):
        return f"<EventGuest {self.first_name} {self.last_name} event={self.event_id}>"


class EventBuddyPair(Base):
    """Battle buddy pairing — event-scoped, reciprocal — PP-070c."""

    __tablename__ = "event_buddy_pairs"
    __table_args__ = (
        UniqueConstraint("event_id", "member_a_id", name="uq_buddy_member_a"),
        UniqueConstraint("event_id", "member_b_id", name="uq_buddy_member_b"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    member_a_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)
    member_b_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)
    guest_a_id: Mapped[Optional[int]] = mapped_column(ForeignKey("event_guests.id"), nullable=True)
    guest_b_id: Mapped[Optional[int]] = mapped_column(ForeignKey("event_guests.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event: Mapped["Event"] = relationship(back_populates="buddy_pairs")

    def __repr__(self):
        return f"<EventBuddyPair event={self.event_id} A={self.member_a_id} B={self.member_b_id}>"


class EventGuardSlot(Base):
    """Guard duty slot configuration for an event — PP-070d."""

    __tablename__ = "event_guard_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    slot_number: Mapped[int] = mapped_column(Integer)
    slot_label: Mapped[Optional[str]] = mapped_column(String(32))  # e.g., "2200-0000"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event: Mapped["Event"] = relationship(back_populates="guard_slots")
    assignments: Mapped[list["EventGuardDuty"]] = relationship(back_populates="slot", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<EventGuardSlot event={self.event_id} slot={self.slot_number} label={self.slot_label}>"


class EventGuardDuty(Base):
    """Guard duty slot assignment (member or guest → slot) — PP-070d."""

    __tablename__ = "event_guard_duty"
    __table_args__ = (
        UniqueConstraint("event_id", "slot_number", "member_id", name="uq_guard_event_slot_member"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    slot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("event_guard_slots.id"), nullable=True)
    slot_number: Mapped[int] = mapped_column(Integer)
    slot_label: Mapped[Optional[str]] = mapped_column(String(32))
    member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)
    guest_id: Mapped[Optional[int]] = mapped_column(ForeignKey("event_guests.id"), nullable=True)
    assigned_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event: Mapped["Event"] = relationship(back_populates="guard_duties")
    slot: Mapped[Optional["EventGuardSlot"]] = relationship(back_populates="assignments")

    def __repr__(self):
        return f"<EventGuardDuty event={self.event_id} slot={self.slot_number} member={self.member_id}>"


class EventVexillation(Base):
    """Mission team (vexillatio) — event-scoped, does NOT alter permanent assignment — PP-070e."""

    __tablename__ = "event_vexillations"
    __table_args__ = (
        UniqueConstraint("event_id", "name", name="uq_vex_event_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    name: Mapped[str] = mapped_column(String(64))
    commander_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)

    # field_status: in_assembly, in_field, released
    field_status: Mapped[str] = mapped_column(String(16), default="in_assembly")

    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event: Mapped["Event"] = relationship(back_populates="vexillations")
    assignments: Mapped[list["EventVexillationAssignment"]] = relationship(
        back_populates="vexillation", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<EventVexillation {self.name} event={self.event_id} status={self.field_status}>"


class EventVexillationAssignment(Base):
    """Member or guest assigned to a vexillation — PP-070e."""

    __tablename__ = "event_vexillation_assignments"
    __table_args__ = (
        UniqueConstraint("event_id", "member_id", name="uq_vex_assign_member"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    vexillation_id: Mapped[int] = mapped_column(ForeignKey("event_vexillations.id"))
    member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("members.id"), nullable=True)
    guest_id: Mapped[Optional[int]] = mapped_column(ForeignKey("event_guests.id"), nullable=True)
    assigned_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    vexillation: Mapped["EventVexillation"] = relationship(back_populates="assignments")

    def __repr__(self):
        return f"<EventVexillationAssignment vex={self.vexillation_id} member={self.member_id}>"
