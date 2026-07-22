"""Shop (S1–S6 staff section) model — DB-backed single source of truth.

Replaces the shop catalog that was defined 4–5× and had drifted:
  - shops.SHOP_SIGNUP_CATALOG (key/name/icon/desc)
  - shops.SHOP_META (short name/icon/has_dashboard)
  - shops.SHOP_ROLE (key -> portal role)
  - shops.SHOP_ACCESS (per-shop RBAC role set)
  - chain_of_command.SHOP_DEFS (key/label/icon)
  - constants.RECIPIENT_GROUPS s1..s6 (label + roles)

Keyed by `key` ("S1".."S6", stable). Renaming a shop, changing its icon,
toggling a dashboard, or adjusting who can access it is now one row edit
(soon via AdminCP) instead of a 5-file change.

`access_roles` is a comma-separated portal-role list (RBAC): who may view/act
in the shop beyond command/admin. `role` is the shop's own portal role ("s1").
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable key: "S1".."S6" (also add S7+ here later). Unique.
    key: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    # Canonical full name, e.g. "S3 — Operations & Training".
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Short display name, e.g. "S3 — Ops & Training".
    short_name: Mapped[str] = mapped_column(String(48), nullable=False)
    icon: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The shop's own portal role, e.g. "s3".
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # Comma-separated portal roles that may access this shop (RBAC). Command/
    # admin are always implied by the service; extras (e.g. "leader" on S3) live here.
    access_roles: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    has_dashboard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=99)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
