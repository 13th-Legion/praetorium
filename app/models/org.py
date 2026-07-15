"""Org-structure models — chain-of-command reporting map (PP-243).

The shop→command reporting relationship is not derivable from member billets,
so it is stored here as editable configuration. One row per S-shop.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ShopReporting(Base):
    """Per-shop reporting configuration for the chain-of-command chart.

    - shop_key: canonical shop id ("S1".."S6"); unique.
    - reports_to_member_id: the command member this shop's lead reports to.
      NULL when command_led is True (shop is led by a command member itself,
      so there is no separate "reports to" — it is the top of that chain).
    - command_led: True for shops led by a command member (e.g. S3=CO, S6=1SG).
    - note: optional short caption shown on the "command-led" strip.
    """

    __tablename__ = "shop_reporting"

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_key: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    reports_to_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    command_led: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ShopSignupRequest(Base):
    """Patched-member request to join an S-shop, routed to the shop head(s).

    A member browses the shop sign-up page, picks a shop, and submits an
    optional note. The request lands as 'pending' and is reviewed by the
    shop's lead (billet 'Sn:...(Lead)') or Command/Admin, who accept or
    decline. On accept, the member's primary_billet gains the shop and the
    NC shop group is synced (via member_edit._sync_shop_groups).
    """

    __tablename__ = "shop_signup_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    shop_key: Mapped[str] = mapped_column(String(8), nullable=False)  # 'S1'..'S6'
    message: Mapped[Optional[str]] = mapped_column(Text)  # applicant's optional note
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending, accepted, declined
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    review_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ShopSignupRequest member={self.member_id} shop={self.shop_key} status={self.status}>"
