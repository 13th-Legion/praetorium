"""Urban Evasion 2 — exercise, teams, participants, evolutions, live scoring.

Live-scoring exercise module. Participants are pulled from a portal event's
RSVPs, split into two teams, given unique start/end points, and rotated through
two evolutions where the teams swap roles (targets ↔ spotters). Spotters log
scoring events from the field; targets are pass/fail (reach endpoint undetected).
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UrbanEvasionExercise(Base):
    __tablename__ = "ue2_exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), unique=True, nullable=True)
    date_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    date_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    radio_primary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    radio_alternate: Mapped[str | None] = mapped_column(String(64), nullable=True)
    radio_realworld: Mapped[str | None] = mapped_column(String(64), nullable=True)
    team_a_name: Mapped[str] = mapped_column(String(64), default="Blue")
    team_b_name: Mapped[str] = mapped_column(String(64), default="Gold")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class UrbanEvasionParticipant(Base):
    __tablename__ = "ue2_participants"
    __table_args__ = (
        UniqueConstraint("exercise_id", "member_id", name="uq_ue2_participant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("ue2_exercises.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    team: Mapped[str] = mapped_column(String(1))  # "a" | "b"
    start_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    end_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    buddy_id: Mapped[int | None] = mapped_column(ForeignKey("ue2_participants.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class UrbanEvasionEvolution(Base):
    __tablename__ = "ue2_evolutions"
    __table_args__ = (
        UniqueConstraint("exercise_id", "seq", name="uq_ue2_evolution"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("ue2_exercises.id"))
    seq: Mapped[int] = mapped_column(Integer)  # 1 | 2
    time_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    team_a_role: Mapped[str] = mapped_column(String(16))  # "spotter" | "target"
    team_b_role: Mapped[str] = mapped_column(String(16))  # "spotter" | "target"


class UrbanEvasionScoreEvent(Base):
    __tablename__ = "ue2_score_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("ue2_exercises.id"))
    evolution_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("ue2_participants.id"))
    kind: Mapped[str] = mapped_column(String(32))
    target_participant_id: Mapped[int | None] = mapped_column(
        ForeignKey("ue2_participants.id"), nullable=True
    )
    points: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
