"""Election system models — PP-077 CO Election.

Anonymity guarantee:
- election_ballots has NO foreign key to members (only to nominee)
- election_voter_roll has NO foreign key to ballots
- Both written in same transaction but with no linking column
- Ballot timestamps coarsened to nearest hour to prevent timing correlation
- election_nomination_receipts has NO link to which nominee was selected
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String, DateTime, Boolean, Integer,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Election(Base):
    """An election lifecycle record."""

    __tablename__ = "elections"

    id: Mapped[int] = mapped_column(primary_key=True)

    title: Mapped[str] = mapped_column(String(128))

    # Phase: nominations, voting, runoff, complete, cancelled
    phase: Mapped[str] = mapped_column(String(16), default="nominations")

    nominations_open: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    nominations_close: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    voting_open: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    voting_close: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Minimum participation % required for a valid result (e.g. 75)
    quorum_pct: Mapped[int] = mapped_column(Integer, default=75)

    # Snapshot of eligible voters taken at voting_open
    eligible_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Audit
    created_by: Mapped[str] = mapped_column(String(64))  # NC username
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # If this is a runoff, points to the original election
    runoff_of: Mapped[Optional[int]] = mapped_column(
        ForeignKey("elections.id"), nullable=True
    )

    # Relationships
    nominations: Mapped[list["ElectionNomination"]] = relationship(
        back_populates="election", cascade="all, delete-orphan"
    )
    nomination_receipts: Mapped[list["ElectionNominationReceipt"]] = relationship(
        back_populates="election", cascade="all, delete-orphan"
    )
    ballots: Mapped[list["ElectionBallot"]] = relationship(
        back_populates="election", cascade="all, delete-orphan"
    )
    voter_roll: Mapped[list["ElectionVoterRoll"]] = relationship(
        back_populates="election", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Election {self.id}: {self.title} ({self.phase})>"


class ElectionNomination(Base):
    """A nomination record — tracks who was nominated, not who nominated them.

    Nominations are anonymous: nominator_id is intentionally absent.
    """

    __tablename__ = "election_nominations"
    __table_args__ = (
        UniqueConstraint("election_id", "nominee_id", name="uq_election_nominee"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("elections.id"))
    nominee_id: Mapped[int] = mapped_column(ForeignKey("members.id"))

    nominated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # None = pending response, True = accepted, False = declined
    accepted: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    election: Mapped["Election"] = relationship(back_populates="nominations")
    nominee: Mapped["Member"] = relationship(foreign_keys=[nominee_id])

    def __repr__(self):
        status = {None: "pending", True: "accepted", False: "declined"}.get(self.accepted, "?")
        return f"<ElectionNomination election={self.election_id} nominee={self.nominee_id} {status}>"


class ElectionNominationReceipt(Base):
    """Tracks that a member submitted a nomination — NOT which nominee they picked.

    Prevents double-nomination. Has no link to ElectionNomination by design.
    """

    __tablename__ = "election_nomination_receipts"
    __table_args__ = (
        UniqueConstraint("election_id", "member_id", name="uq_election_nominator"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("elections.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    nominated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    election: Mapped["Election"] = relationship(back_populates="nomination_receipts")

    def __repr__(self):
        return f"<ElectionNominationReceipt election={self.election_id} member={self.member_id}>"


class ElectionBallot(Base):
    """Anonymous ballot box — contains only the vote and a coarsened timestamp.

    NO voter_id column — cannot be linked to a specific voter by design.
    Timestamps are rounded to the nearest hour before storage.
    """

    __tablename__ = "election_ballots"

    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("elections.id"))

    # Who they voted FOR (the nominee) — NOT who cast the ballot
    nominee_id: Mapped[int] = mapped_column(ForeignKey("members.id"))

    # Coarsened to nearest hour — prevents timing correlation
    cast_at: Mapped[datetime] = mapped_column(DateTime)

    # Relationships
    election: Mapped["Election"] = relationship(back_populates="ballots")
    nominee: Mapped["Member"] = relationship(foreign_keys=[nominee_id])

    def __repr__(self):
        return f"<ElectionBallot election={self.election_id} nominee={self.nominee_id}>"


class ElectionVoterRoll(Base):
    """Tracks WHO voted — not HOW they voted.

    Has no link to ElectionBallot by design. Prevents double-voting while
    maintaining ballot anonymity.
    """

    __tablename__ = "election_voter_roll"
    __table_args__ = (
        UniqueConstraint("election_id", "member_id", name="uq_election_voter"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    election_id: Mapped[int] = mapped_column(ForeignKey("elections.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    voted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    election: Mapped["Election"] = relationship(back_populates="voter_roll")

    def __repr__(self):
        return f"<ElectionVoterRoll election={self.election_id} member={self.member_id}>"
