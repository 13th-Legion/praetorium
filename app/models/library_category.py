"""library_categories — DB-backed publication taxonomy (single source of truth).

Replaces the hardcoded LIBRARY_CATEGORIES list in training_library.py (13LG/
TSM/FM/TC/ATP/TM/Other). Editable publication categories for the battle library.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LibraryCategory(Base):
    __tablename__ = "library_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)   # display name
    icon: Mapped[str] = mapped_column(String(16), nullable=False, default="📚")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=99)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
