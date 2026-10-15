"""TSM unit application routing — which destination a unit's applications go to.

Each row maps a unit (the label of the state application form's unit option)
to either "deck" (the 13th Legion pipeline) or an email address (forwarded to
that unit's S1). Managed from the S1 admin page.
"""

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CompanyRouting(Base):
    __tablename__ = "company_routing"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_name: Mapped[str] = mapped_column(unique=True)
    route_type: Mapped[str] = mapped_column()  # "deck" | "email"
    email: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
