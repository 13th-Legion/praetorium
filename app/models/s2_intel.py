"""S2 Intelligence & Security — IIR reports, challenge/password, training sites.

Three S2-owned feature areas (see `projects/s2-dashboard.md`):

1. **IIR** (Intelligence Information Report) — the finished, sourced, analytical
   product. Full field set, two dissemination tiers (unit-wide vs
   S2/Command-only), active/archived lifecycle.
2. **S2ChallengePassword** — recognition/OPSEC signals with a rotation schedule
   (per-mission and/or time-windowed, per Cav 2026-09-24).
3. **S2TrainingSite + S2TrainingSiteMap** — DB-backed training-site library
   (replaces the hardcoded `app/training_sites.py` dict) so S2 can add/remove
   sites and upload maps, feeding the FTX builder's training-site dropdown.
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class IIR(Base):
    """Intelligence Information Report — the analytical intel product."""
    __tablename__ = "s2_iirs"

    id = Column(Integer, primary_key=True, index=True)
    # Auto-generated unique report number, e.g. "IIR-0001".
    report_number = Column(String(32), unique=True, index=True, nullable=False)
    # DTG — date-time-group string rendered at creation, e.g. "241800Z SEP 26".
    dtg = Column(String(24), nullable=False)

    country_area = Column(String(120), nullable=False)      # geographic scope
    subject = Column(String(200), nullable=False)            # short title line
    source = Column(Text, nullable=True)                     # who/where it came from
    reliability_rating = Column(String(24), nullable=True)   # A–F or high/med/low
    credibility = Column(String(24), nullable=True)          # analytic confidence

    details = Column(Text, nullable=False)                   # substantive body (Quill HTML)
    assessment = Column(Text, nullable=True)                 # analyst evaluation
    remarks = Column(Text, nullable=True)                    # admin/dissemination caveats

    # unit_wide = front-page + bell to everyone; command_only = S2/Command only
    dissemination_tier = Column(String(16), default="command_only", nullable=False)
    status = Column(String(16), default="active", nullable=False)  # active, archived

    author_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    author = relationship("Member", foreign_keys=[author_id])

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    archived_at = Column(DateTime, nullable=True)


class S2ChallengePassword(Base):
    """A challenge/password/running-password set with an optional validity window.

    Per-mission (event_id) and/or time-windowed (valid_from/valid_until) — both
    are supported; either or both may be null.
    """
    __tablename__ = "s2_challenge_passwords"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(80), nullable=True)  # e.g. "Sat 0600–1200" or "September FTX"
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=True)

    challenge = Column(String(120), nullable=False)          # what the sentry asks
    password = Column(String(120), nullable=False)           # the correct reply
    running_password = Column(String(120), nullable=True)    # alternate for returning patrols

    valid_from = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)
    active = Column(Boolean, default=True, nullable=False)

    created_by = Column(String(64), nullable=True)  # NC username
    created_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("Event")


class S2TrainingSite(Base):
    """A field training location (Able, Baker, Charlie, Dog, Easy, …)."""
    __tablename__ = "s2_training_sites"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(32), unique=True, index=True, nullable=False)  # slug: "able"
    name = Column(String(64), nullable=False)
    nickname = Column(String(64), nullable=True)
    address = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    maps = relationship("S2TrainingSiteMap", back_populates="site",
                        cascade="all, delete-orphan", order_by="S2TrainingSiteMap.id")


class S2TrainingSiteMap(Base):
    """A map (PDF/image) attached to a training site."""
    __tablename__ = "s2_training_site_maps"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("s2_training_sites.id", ondelete="CASCADE"), nullable=False)
    label = Column(String(64), nullable=False)  # e.g. "1:10,000", "1:25,000 (Marked)"
    url = Column(Text, nullable=False)          # WebDAV path or static URL

    created_at = Column(DateTime, default=datetime.utcnow)

    site = relationship("S2TrainingSite", back_populates="maps")
