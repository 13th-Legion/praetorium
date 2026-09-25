"""Event attendance: ops-console check-in vs official `attended`.

Two flags on EventRSVP:
  * checked_in — day-of, set by QR / ops console
  * attended   — official record (FTX count, ribbons, TRADOC credit)

Finalize copies remaining check-ins onto attended. Un-checking a person
must clear BOTH, otherwise finalize puts them right back.
"""

from datetime import datetime

from sqlalchemy import select, func, and_, delete, update

from app.models.events import Event, EventRSVP
from app.models.member import Member
from app.models.training import MemberTradoc


def is_present(rsvp: EventRSVP) -> bool:
    return bool(rsvp.attended or rsvp.checked_in)


def is_no_show(rsvp: EventRSVP) -> bool:
    """Explicit no-show flag (attending RSVP, confirmed absent)."""
    return bool(rsvp.no_show)


async def mark_no_show(db, event: Event, rsvp: EventRSVP) -> None:
    """Mark a member as a no-show: clears any check-in/attendance and sets the flag.

    No-show means they never scanned AND are not present; clear both day-of and
    official attendance so Finalize cannot resurrect them, then set no_show.
    """
    rsvp.no_show = True
    rsvp.checked_in = False
    rsvp.checked_in_at = None
    rsvp.checked_in_by = None
    rsvp.attended = False
    rsvp.updated_at = datetime.utcnow()
    await reverse_auto_credits(db, event, rsvp.member_id)
    await recompute_ftx_counters(db, rsvp.member_id)


async def clear_no_show(rsvp: EventRSVP) -> None:
    """Clear the no-show flag (e.g. when S1 later marks them present)."""
    rsvp.no_show = False
    rsvp.updated_at = datetime.utcnow()


def event_ftx_date(event: Event):
    ds = event.date_start
    return ds.date() if ds is not None and hasattr(ds, "date") else ds


async def revoke_rsvp_attendance(db, event: Event, rsvp: EventRSVP) -> None:
    """Clear day-of check-in AND official attendance for one RSVP.

    Also drops this-event auto TRADOC credits (no-op if none) and recomputes
    the denormalized ftx_count / last_ftx from remaining attended FTX rows.
    Safe to call before or after finalize.
    """
    rsvp.checked_in = False
    rsvp.checked_in_at = None
    rsvp.checked_in_by = None
    rsvp.attended = False
    rsvp.updated_at = datetime.utcnow()
    await reverse_auto_credits(db, event, rsvp.member_id)
    await recompute_ftx_counters(db, rsvp.member_id)


async def reverse_auto_credits(db, event: Event, member_id: int) -> int:
    """Delete auto-credited TRADOC rows created by finalizing this event."""
    ftx_date = event_ftx_date(event)
    note = f"Auto-credited: {event.title}"
    result = await db.execute(
        delete(MemberTradoc).where(
            and_(
                MemberTradoc.member_id == member_id,
                MemberTradoc.signed_off_by == "auto",
                MemberTradoc.ftx_date == ftx_date,
                MemberTradoc.notes == note,
            )
        )
    )
    return result.rowcount or 0


async def recompute_ftx_counters(db, member_id: int) -> None:
    """Recompute Member.ftx_count / last_ftx from attended ftx/mcftx RSVPs."""
    count, last_dt = (await db.execute(
        select(func.count(EventRSVP.id), func.max(Event.date_start))
        .select_from(EventRSVP)
        .join(Event, Event.id == EventRSVP.event_id)
        .where(
            and_(
                EventRSVP.member_id == member_id,
                EventRSVP.attended == True,  # noqa: E712
                Event.category.in_(["ftx", "mcftx"]),
            )
        )
    )).one()
    last_date = last_dt.date() if last_dt is not None and hasattr(last_dt, "date") else last_dt

    await db.execute(
        update(Member).where(Member.id == member_id).values(
            ftx_count=int(count or 0),
            last_ftx=last_date,
            updated_at=datetime.utcnow(),
        )
    )
