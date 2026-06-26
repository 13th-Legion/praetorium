"""Newsletter section templates — one-click reusable section blocks for the
Legionary Dispatch composer.

Two categories:
- per_issue: insert header + empty/placeholder body (content changes each issue)
- recurring: insert header + pre-filled boilerplate (static, links → Praetorium)

DB-backed so S1 can edit the library (fix links, update boilerplate) in one place
and every future newsletter starts from current-correct content.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, Integer, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NewsletterSectionTemplate(Base):
    __tablename__ = "newsletter_section_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160))           # ◆ section header text
    body_html: Mapped[str] = mapped_column(Text, default="")  # boilerplate (recurring) or prompt (per_issue)
    category: Mapped[str] = mapped_column(String(20), default="per_issue")  # per_issue | recurring
    default_order: Mapped[int] = mapped_column(Integer, default=100)
    # Pre-load into every new newsletter draft (the recurring bottom block).
    preload: Mapped[bool] = mapped_column(Boolean, default=False)
    # Dynamic sections render live content at compose time (e.g. training calendar
    # pulled from Praetorium events). dynamic_source names the generator.
    dynamic_source: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
