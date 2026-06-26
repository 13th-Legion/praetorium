"""Legionary Dispatch — unit newsletter models.

Extends the Unit Comms (email-blast) feature with full newsletters:
inline hosted images, file attachments, seasonal crest mastheads,
send-now and scheduled delivery, and a self-building archive.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Integer, ForeignKey, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Newsletter(Base):
    __tablename__ = "newsletters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Masthead / identity
    title: Mapped[str] = mapped_column(String(200))          # "Legionary Dispatch — November 2025"
    subject: Mapped[str] = mapped_column(String(300))        # email subject line
    crest_key: Mapped[str] = mapped_column(String(64), default="standard")  # seasonal crest catalog key

    # Body — sanitized HTML produced by the Quill editor (hosted image URLs already substituted)
    body_html: Mapped[str] = mapped_column(Text, default="")

    # Recipient targeting — comma-separated EMAIL_BLAST_GROUPS keys (e.g. "entire_unit,leaders")
    groups_csv: Mapped[str] = mapped_column(String(300), default="")

    # Lifecycle: draft -> scheduled -> sending -> sent | failed | canceled
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)

    # Scheduling (UTC). NULL = send-now / no schedule.
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Delivery results
    recipient_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)

    # Archive — Nextcloud path of the rendered copy filed after send
    archive_path: Mapped[Optional[str]] = mapped_column(String(500))

    # Authorship / audit
    created_by: Mapped[str] = mapped_column(String(120), default="")       # nc username
    created_by_name: Mapped[str] = mapped_column(String(160), default="")  # display name (signature)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    images: Mapped[list["NewsletterImage"]] = relationship(
        back_populates="newsletter", cascade="all, delete-orphan"
    )
    attachments: Mapped[list["NewsletterAttachment"]] = relationship(
        back_populates="newsletter", cascade="all, delete-orphan"
    )


class NewsletterImage(Base):
    """Hosted inline image referenced from body_html. Stored on disk under the
    portal static newsletter dir so it renders in remote mail clients
    (base64 inline images are stripped by Gmail)."""
    __tablename__ = "newsletter_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    newsletter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("newsletters.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))     # stored filename on disk
    orig_name: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="image/png")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    newsletter: Mapped[Optional["Newsletter"]] = relationship(back_populates="images")


class NewsletterAttachment(Base):
    """File attachment (PDF, etc.) sent as a real MIME part with the email."""
    __tablename__ = "newsletter_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    newsletter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("newsletters.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))     # stored filename on disk
    orig_name: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    newsletter: Mapped[Optional["Newsletter"]] = relationship(back_populates="attachments")
