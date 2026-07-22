"""Lightweight async DB builders for tests (no factory_boy dependency).

Each helper inserts a row into the provided AsyncSession and returns the ORM
object. Sensible defaults; override any field via kwargs.
"""

from datetime import datetime

from app.models.member import Member
from app.models.events import Event, EventRSVP


_seq = {"n": 0}


def _next() -> int:
    _seq["n"] += 1
    return _seq["n"]


async def make_member(db, **kw) -> Member:
    n = _next()
    defaults = dict(
        nc_username=f"user{n}",
        first_name=kw.pop("first_name", f"First{n}"),
        last_name=kw.pop("last_name", f"Last{n}"),
        rank_grade="E-4",
        status="active",
        ftx_count=0,
    )
    defaults.update(kw)
    m = Member(**defaults)
    db.add(m)
    await db.flush()
    return m


async def make_event(db, **kw) -> Event:
    n = _next()
    defaults = dict(
        title=kw.pop("title", f"Event {n}"),
        category="ftx",
        date_start=datetime(2026, 6, 1, 6, 0),
        created_by="test",
    )
    defaults.update(kw)
    e = Event(**defaults)
    db.add(e)
    await db.flush()
    return e


async def make_rsvp(db, event, member, attended=True, status="attending", **kw) -> EventRSVP:
    defaults = dict(
        event_id=event.id,
        member_id=member.id,
        status=status,
        attended=attended,
    )
    defaults.update(kw)
    r = EventRSVP(**defaults)
    db.add(r)
    await db.flush()
    return r
