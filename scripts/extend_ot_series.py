"""Extend the weekly Online Training series so a full Leaders rotation fits.

Clones the last occurrence forward weekly (Tue 2000 CDT pattern preserved by
simply adding 7 days to date_start/date_end). instructor_id is intentionally
left NULL -- the S3 Instructor Rotation tool assigns those.

Usage:
    python3 scripts/extend_ot_series.py --count 14 [--apply]
"""

import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.database import async_session
from app.models.events import Event

SERIES_ID = "a4c22041fca04b6fa0b0bbd606a383a2"


async def main(count: int, apply: bool) -> None:
    async with async_session() as db:
        result = await db.execute(
            select(Event)
            .where(Event.series_id == SERIES_ID)
            .order_by(Event.date_start.desc())
        )
        events = list(result.scalars().all())
        if not events:
            print("series not found")
            return

        last = events[0]
        print(f"series has {len(events)} occurrences; last = "
              f"{last.date_start:%Y-%m-%d %H:%M} (event {last.id})")

        duration = (last.date_end - last.date_start) if last.date_end else None
        cur = last.date_start
        created = []
        for _ in range(count):
            cur = cur + timedelta(days=7)
            ev = Event(
                title=last.title,
                category=last.category,
                description=last.description,
                location=last.location,
                meeting_mode=last.meeting_mode,
                talk_token=last.talk_token,
                date_start=cur,
                date_end=(cur + duration) if duration else None,
                status="active",
                rsvp_enabled=last.rsvp_enabled,
                training_block=last.training_block,
                training_blocks=last.training_blocks,
                instructor_id=None,          # rotation tool fills this
                invite_groups=last.invite_groups,
                series_id=SERIES_ID,
                is_series_master=False,
                created_by="spooky (series extend)",
            )
            created.append(ev)
            if apply:
                db.add(ev)

        for ev in created:
            print(f"  + {ev.date_start:%a %Y-%m-%d %H:%M}")

        if apply:
            await db.commit()
            print(f"\nAPPLIED: created {len(created)} occurrences.")
        else:
            print(f"\nDRY RUN: would create {len(created)} occurrences. "
                  f"Re-run with --apply to commit.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=14)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.count, a.apply))
