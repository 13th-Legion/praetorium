"""Ribbon system models — catalog + member awards.

Four display tiers (see RIBBON_SYSTEM_SPEC.md):
  dona   — Dona Militaria decorations (Gladius/Corona/Phalerae)
  rack   — precedence-ordered ribbon rack (achievements)
  tab    — qualification tabs (Sabre/Marksman/Sharpshooter/Equites)
  tenure — Anni Stipendiorum service discs (computed from join_date; catalog
           rows exist only to hold point values)
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, Integer, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RibbonCatalog(Base):
    """Single source of truth for every awardable ribbon/decoration/tab.

    `code` is the stable key (matches the art filename stem, e.g. 'ftx',
    'gladius_aurum'). `section` selects which profile tier it renders in.
    """

    __tablename__ = "ribbon_catalog"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(96))
    section: Mapped[str] = mapped_column(String(8))  # dona | rack | tab | tenure
    precedence: Mapped[int] = mapped_column(Integer, default=99)
    base_points: Mapped[int] = mapped_column(Integer, default=0)
    device_increment: Mapped[int] = mapped_column(Integer, default=0)  # pts per device tier
    max_devices: Mapped[int] = mapped_column(Integer, default=0)       # 0 = unlimited
    image: Mapped[Optional[str]] = mapped_column(String(160))          # static path
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)      # auto-awarded by rules
    claimable: Mapped[bool] = mapped_column(Boolean, default=True)     # member can request historical
    description: Mapped[Optional[str]] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self):
        return f"<RibbonCatalog {self.code} sec={self.section} prec={self.precedence}>"


class MemberRibbon(Base):
    """A ribbon/decoration/tab bestowed on a member.

    `device_count` = number of additional-award devices earned (tiered ribbons).
    `source` tracks provenance: auto rule, manual grant, or approved claim.
    """

    __tablename__ = "member_ribbons"
    __table_args__ = (UniqueConstraint("member_id", "ribbon_code", name="uq_member_ribbon"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    ribbon_code: Mapped[str] = mapped_column(ForeignKey("ribbon_catalog.code"), index=True)
    device_count: Mapped[int] = mapped_column(Integer, default=0)
    awarded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    awarded_by: Mapped[Optional[str]] = mapped_column(String(64))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(8), default="manual")  # auto | manual | claim

    def __repr__(self):
        return f"<MemberRibbon m={self.member_id} {self.ribbon_code} dev={self.device_count}>"
