#!/usr/bin/env python3
"""Backfill member geocoordinates + geo team zones.

Geocodes every active/recruit member that has an address (or zip) but no
latitude, using the same logic as the live edit routes. Rate-limited to be
polite to Nominatim (1 req/sec). Idempotent — only fills missing coords
unless --all is passed (which re-geocodes everyone).

Run inside the app container:
  docker exec -it praetorium-app python3 /opt/praetorium/scripts/backfill_geocode.py
  docker exec -it praetorium-app python3 /opt/praetorium/scripts/backfill_geocode.py --all
"""

import asyncio
import sys
import time

from sqlalchemy import select, and_, or_

from app.database import async_session
from app.models.member import Member
from app.geo import geocode_member_fields, assign_zone


async def main(regeocode_all: bool = False):
    async with async_session() as db:
        if regeocode_all:
            stmt = select(Member).where(
                or_(Member.address.isnot(None), Member.zip_code.isnot(None))
            )
        else:
            stmt = select(Member).where(
                and_(
                    Member.latitude.is_(None),
                    or_(Member.address.isnot(None), Member.zip_code.isnot(None)),
                )
            )
        members = (await db.execute(stmt)).scalars().all()
        print(f"Members to geocode: {len(members)}")

        ok = fail = moved = 0
        for i, m in enumerate(members, 1):
            lat, lon = geocode_member_fields(m.address, m.city, m.state, m.zip_code)
            if lat is not None:
                m.latitude = lat
                m.longitude = lon
                new_team, bearing = assign_zone(lat, lon)
                old_team = m.team
                if new_team and new_team != old_team:
                    m.team = new_team
                    moved += 1
                ok += 1
                print(f"  [{i}/{len(members)}] OK  {m.last_name:<16} -> {lat:.5f},{lon:.5f} "
                      f"team {old_team}->{m.team} (brg {bearing:.0f})")
            else:
                fail += 1
                print(f"  [{i}/{len(members)}] MISS {m.last_name:<16} "
                      f"addr={m.address!r} zip={m.zip_code!r}")
            await db.commit()
            # Politeness: Nominatim asks for <=1 req/sec; Census is fine but this is safe.
            time.sleep(1.1)

        print(f"\nDone. geocoded={ok} missed={fail} team_reassigned={moved}")


if __name__ == "__main__":
    asyncio.run(main(regeocode_all="--all" in sys.argv))
