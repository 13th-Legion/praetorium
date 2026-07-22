"""Ribbon / promotion-point computation.

Single source of truth for turning a member's ribbons + tenure into point
totals. Two totals are surfaced everywhere:

  lifetime            — sum of every award's points (base + device increments)
                        plus tenure-disc points computed from join_date.
  since_last_promotion — same, but only awards dated after the member's most
                        recent promotion (from rank_history).

Tenure (Anni Stipendiorum) is NOT stored per-member; it is derived from
join_date with a greedy gold/silver/bronze fill (gold=5yr, silver=3yr,
bronze=1yr). Tenure points count toward lifetime; for "since last promotion"
we only credit whole discs newly crossed since the promo date.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ribbons import RibbonCatalog, MemberRibbon
from app.models.rank_history import RankHistory


# ── Tenure disc math ─────────────────────────────────────────────────────────
TENURE_POINTS = {"gold": 15, "silver": 9, "bronze": 3}


def tenure_discs(years: int) -> dict[str, int]:
    """Greedy fill: gold=years//5, then silver=rem//3, then bronze=rem."""
    if years < 0:
        years = 0
    gold = years // 5
    rem = years - gold * 5
    silver = rem // 3
    bronze = rem - silver * 3
    return {"gold": gold, "silver": silver, "bronze": bronze}


def years_of_service(join: Optional[date], asof: Optional[date] = None) -> int:
    if not join:
        return 0
    asof = asof or date.today()
    yrs = asof.year - join.year - ((asof.month, asof.day) < (join.month, join.day))
    return max(0, yrs)


def tenure_points(join: Optional[date], asof: Optional[date] = None) -> int:
    d = tenure_discs(years_of_service(join, asof))
    return sum(TENURE_POINTS[k] * n for k, n in d.items())


def ribbon_award_points(cat: RibbonCatalog, device_count: int) -> int:
    """Points for one held ribbon = base + device_increment * device_count."""
    return cat.base_points + cat.device_increment * max(0, device_count)


@dataclass
class PointsBreakdown:
    lifetime: int = 0
    since_last_promotion: int = 0
    last_promotion_date: Optional[datetime] = None
    lifetime_tenure: int = 0
    lifetime_ribbons: int = 0
    detail: list = field(default_factory=list)  # [(code, name, points, awarded_at, since?)]


async def _last_promotion_date(db: AsyncSession, member_id: int) -> Optional[datetime]:
    """Most recent rank-UP effective date. Demotions/patches ignored for cutoff
    purposes are still valid promo markers; we just take the latest rank change
    that increased grade. Fallback: latest rank_history row of any kind."""
    rows = (await db.execute(
        select(RankHistory)
        .where(RankHistory.member_id == member_id)
        .order_by(RankHistory.effective_date.desc())
    )).scalars().all()
    if not rows:
        return None
    # Prefer the latest promotion (new grade higher than old); else latest row.
    from app.services import ranks as _ranks
    _idx = _ranks.index_map()
    for r in rows:
        oi = _idx.get(r.old_rank, -1)
        ni = _idx.get(r.new_rank, -1)
        if ni > oi:
            return r.effective_date
    return rows[0].effective_date


async def compute_member_points(
    db: AsyncSession,
    member_id: int,
    join_date: Optional[date],
) -> PointsBreakdown:
    """Full lifetime + since-last-promotion breakdown for a member."""
    out = PointsBreakdown()
    out.last_promotion_date = await _last_promotion_date(db, member_id)
    cutoff = out.last_promotion_date

    # Catalog lookup
    cat_rows = (await db.execute(select(RibbonCatalog))).scalars().all()
    cat = {c.code: c for c in cat_rows}

    # Merge stored (manual/claim) + derived (auto) ribbons. Manual wins on code.
    mrs = (await db.execute(
        select(MemberRibbon).where(MemberRibbon.member_id == member_id)
    )).scalars().all()
    merged: dict[str, dict] = {}
    for mr in mrs:
        merged[mr.ribbon_code] = {"device_count": mr.device_count, "awarded_at": mr.awarded_at}
    # Derived (only if a Member obj is resolvable); compute lazily.
    from app.models.member import Member as _Member
    member_obj = (await db.execute(select(_Member).where(_Member.id == member_id))).scalar_one_or_none()
    if member_obj is not None:
        from app.services.ribbon_derive import derive_ribbons
        for d in await derive_ribbons(db, member_obj):
            if d["code"] not in merged:
                merged[d["code"]] = {"device_count": d["device_count"], "awarded_at": d["awarded_at"]}

    for code, info in merged.items():
        c = cat.get(code)
        if not c or not c.active:
            continue
        pts = ribbon_award_points(c, info["device_count"])
        out.lifetime += pts
        out.lifetime_ribbons += pts
        awarded_at = info["awarded_at"]
        since = bool(cutoff and awarded_at and awarded_at > cutoff)
        if since:
            out.since_last_promotion += pts
        out.detail.append((c.code, c.name, pts, awarded_at, since))

    # Tenure (lifetime always; since-promo credits discs crossed after cutoff)
    out.lifetime_tenure = tenure_points(join_date)
    out.lifetime += out.lifetime_tenure
    if cutoff and join_date:
        pts_now = tenure_points(join_date, date.today())
        pts_at_cut = tenure_points(join_date, cutoff.date() if isinstance(cutoff, datetime) else cutoff)
        out.since_last_promotion += max(0, pts_now - pts_at_cut)

    return out
