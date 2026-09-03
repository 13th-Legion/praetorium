"""Instructor rotation for recurring event series (PP-300).

Assigns instructors across the occurrences of an event series in a fair,
cycle-aware random order:

* The instructor pool is resolved from the SAME 21-group registry used by
  event invites / email blasts (``RECIPIENT_GROUPS`` + dynamic team groups),
  so "Leaders", "Officers", "Team Aquila", "S3", etc. all work as checkboxes.
* Assignment is *cycle-aware*, not naive random: the pool is shuffled and
  dealt one-per-event. When the pool is exhausted it reshuffles, so nobody
  teaches twice until everyone has taught once.
* Recent history in the series is honored, so re-running the tool later
  picks up where the rotation left off instead of repeating people.

The planner is pure (no DB writes) so the UI can preview an assignment
before committing it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select

from app.models.events import Event
from app.models.member import Member


@dataclass
class RotationSlot:
    """One planned assignment (not yet persisted)."""

    event_id: int
    date_start: datetime
    member_id: int
    member_label: str
    previous_instructor_id: Optional[int] = None


def _shuffled(pool: Sequence[int], rng: random.Random) -> list[int]:
    out = list(pool)
    rng.shuffle(out)
    return out


def plan_rotation(
    events: Sequence[Event],
    pool_ids: Sequence[int],
    labels: dict[int, str],
    *,
    recent_history: Sequence[int] = (),
    seed: Optional[int] = None,
) -> list[RotationSlot]:
    """Deal ``pool_ids`` across ``events`` in fair cycle-aware random order.

    ``events`` must already be filtered (e.g. future-only / unassigned-only)
    and sorted by date. ``recent_history`` is the tail of instructor ids most
    recently used in this series (oldest -> newest); those members are pushed
    to the back of the first cycle so the rotation continues rather than
    restarting on the same people.
    """
    if not events or not pool_ids:
        return []

    rng = random.Random(seed)
    pool = list(dict.fromkeys(pool_ids))  # de-dupe, keep order stable

    # Build the first cycle, biased so recently-used instructors come last.
    recent = [m for m in recent_history if m in set(pool)]
    # Most-recent teachers should wait longest -> order by how recently they
    # taught (newest last in `recent`, so reverse to put newest at the end).
    recency_rank = {mid: i for i, mid in enumerate(reversed(recent))}
    fresh = [m for m in pool if m not in recency_rank]
    used = [m for m in pool if m in recency_rank]

    cycle = _shuffled(fresh, rng)
    # Members who taught most recently go last, in reverse-recency order.
    used.sort(key=lambda m: recency_rank[m])
    cycle.extend(used)

    slots: list[RotationSlot] = []
    idx = 0
    for ev in events:
        if idx >= len(cycle):
            # Pool exhausted -> reshuffle for the next full cycle.
            cycle = _shuffled(pool, rng)
            idx = 0
        mid = cycle[idx]
        idx += 1
        slots.append(
            RotationSlot(
                event_id=ev.id,
                date_start=ev.date_start,
                member_id=mid,
                member_label=labels.get(mid, f"#{mid}"),
                previous_instructor_id=ev.instructor_id,
            )
        )
    return slots


async def series_options(db) -> list[dict]:
    """Recurring series that have at least one FUTURE occurrence.

    Returns [{series_id, title, category, total, upcoming, next_date}].
    """
    result = await db.execute(
        select(Event).where(Event.series_id.isnot(None)).order_by(Event.date_start)
    )
    events = result.scalars().all()

    now = datetime.utcnow()
    by_series: dict[str, list[Event]] = {}
    for ev in events:
        by_series.setdefault(ev.series_id, []).append(ev)

    out = []
    for sid, evs in by_series.items():
        upcoming = [e for e in evs if e.date_start >= now]
        if not upcoming:
            continue
        out.append(
            {
                "series_id": sid,
                "title": evs[0].title,
                "category": evs[0].category,
                "total": len(evs),
                "upcoming": len(upcoming),
                "next_date": upcoming[0].date_start,
            }
        )
    out.sort(key=lambda d: d["next_date"])
    return out


async def series_events(
    db, series_id: str, *, future_only: bool = True, only_unassigned: bool = False
) -> list[Event]:
    """Occurrences of a series, sorted by date, with optional filters."""
    conds = [Event.series_id == series_id, Event.status != "cancelled"]
    if future_only:
        conds.append(Event.date_start >= datetime.utcnow())
    if only_unassigned:
        conds.append(Event.instructor_id.is_(None))
    result = await db.execute(select(Event).where(*conds).order_by(Event.date_start))
    return list(result.scalars().all())


async def recent_instructors(db, series_id: str, limit: int = 40) -> list[int]:
    """Instructor ids already used in this series, oldest -> newest (past only)."""
    result = await db.execute(
        select(Event.instructor_id)
        .where(
            Event.series_id == series_id,
            Event.instructor_id.isnot(None),
            Event.date_start < datetime.utcnow(),
        )
        .order_by(Event.date_start)
    )
    ids = [r[0] for r in result.all() if r[0] is not None]
    return ids[-limit:]


async def member_labels(db, member_ids: Sequence[int]) -> dict[int, str]:
    """'SGT Deaton (Crash)' style labels for the preview UI."""
    if not member_ids:
        return {}
    from app.services import ranks as _ranks

    abbr = _ranks.abbr_map()
    result = await db.execute(select(Member).where(Member.id.in_(list(member_ids))))
    out: dict[int, str] = {}
    for m in result.scalars().all():
        rank = abbr.get(m.rank_grade, "") or ""
        cs = f" ({m.callsign})" if m.callsign else ""
        out[m.id] = f"{rank} {m.last_name}{cs}".strip()
    return out
