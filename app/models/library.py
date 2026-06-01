"""Battle Library models — uploaded reference publications (FMs, TCs, ATPs, TMs)."""

from datetime import datetime

from sqlalchemy import String, Integer, DateTime, BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LibraryDocument(Base):
    """An uploaded Battle Library publication stored on disk + metadata in DB."""

    __tablename__ = "library_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(16))  # FM | TC | ATP | TM | Other
    pub_number: Mapped[str] = mapped_column(String(64))  # e.g. "FM 3-24"
    title: Mapped[str] = mapped_column(String(255))
    pub_date: Mapped[str | None] = mapped_column(String(32), nullable=True)  # publication date from cover, e.g. "April 2014"
    filename: Mapped[str] = mapped_column(String(255))  # stored filename on disk (uuid.pdf)
    original_filename: Mapped[str] = mapped_column(String(255))  # uploader filename
    stored_path: Mapped[str] = mapped_column(String(512))  # absolute path under /app/data/library
    file_size: Mapped[int] = mapped_column(BigInteger)  # bytes
    mime_type: Mapped[str] = mapped_column(String(128))
    uploaded_by: Mapped[str] = mapped_column(String(64))  # nc_username
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self):
        return f"<LibraryDocument {self.category}:{self.pub_number}>"
