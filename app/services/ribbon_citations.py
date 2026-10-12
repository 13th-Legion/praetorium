"""One comment per award of a ribbon.

``award_index`` 0 is the ribbon itself. 1 is the first device. A new award
writes its own row and leaves the earlier comments alone.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ribbons import MemberRibbon, RibbonCitation


async def record_citation(
    db: AsyncSession,
    ribbon: MemberRibbon,
    award_index: int,
    reason: str | None,
    awarded_by: str | None,
    awarded_at: datetime | None = None,
) -> RibbonCitation:
    """Write the comment for this award. A blank comment does not erase one."""
    await db.flush()
    text = (reason or "").strip() or None
    existing = (await db.execute(
        select(RibbonCitation).where(
            RibbonCitation.member_ribbon_id == ribbon.id,
            RibbonCitation.award_index == award_index,
        )
    )).scalar_one_or_none()
    if existing:
        if text:
            existing.reason = text
            if awarded_by:
                existing.awarded_by = awarded_by
            existing.awarded_at = awarded_at or datetime.utcnow()
        return existing
    row = RibbonCitation(
        member_ribbon_id=ribbon.id,
        award_index=award_index,
        reason=text,
        awarded_by=awarded_by,
        awarded_at=awarded_at or datetime.utcnow(),
    )
    db.add(row)
    return row


async def citations_for(db: AsyncSession, ribbon_ids: list[int]) -> dict[int, list[str]]:
    """Non-empty comments, oldest award first, keyed by member_ribbons.id."""
    if not ribbon_ids:
        return {}
    rows = (await db.execute(
        select(RibbonCitation)
        .where(RibbonCitation.member_ribbon_id.in_(ribbon_ids))
        .order_by(RibbonCitation.award_index, RibbonCitation.id)
    )).scalars().all()
    out: dict[int, list[str]] = {}
    for row in rows:
        if not row.reason:
            continue
        out.setdefault(row.member_ribbon_id, []).append(row.reason)
    return out
