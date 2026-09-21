#!/usr/bin/env python3
"""Repair city/state/zip for members whose one-line address was never split.

The recruit daemon used to comma-split "Street, City, State ZIP". Application
cards carry the address on one line, usually with no commas, so those members
kept the whole string in `address` with city/zip empty (or, worse, a street
name promoted into `city`).

scripts/backfill_geocode.py only fills MISSING lat/lon -- it never writes
city/zip back, so a backfill left these rows mangled.

Strategy: rebuild the best available one-line address from the columns we
have, re-parse it with the shared parser, and write back ONLY where the
result is an improvement. Never blanks a populated field, never touches
lat/lon or team.

Dry run by default. Pass --apply to write.

    docker exec -i praetorium-app python3 /tmp/backfill_address_fields.py
    docker exec -i praetorium-app python3 /tmp/backfill_address_fields.py --apply
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from sqlalchemy import select  # noqa: E402

from app import database  # noqa: E402
from app.address_parse import parse_oneline_address  # noqa: E402
from app.models.member import Member  # noqa: E402

APPLY = "--apply" in sys.argv


def _looks_like_state_zip(value: str) -> bool:
    """Detect 'TX 75013' wrongly sitting in the city column."""
    if not value:
        return False
    bits = value.replace(",", " ").split()
    if not bits:
        return False
    return len(bits[0]) == 2 and bits[0].isalpha() and bits[0].isupper() and any(
        b.isdigit() and len(b) == 5 for b in bits[1:]
    )


def _recombine(m: Member) -> str:
    """Rebuild a single address line from whatever the row holds."""
    parts = [(m.address or "").strip()]
    city = (m.city or "").strip()
    # A city that is really 'TX 75013' still carries the state/zip we need.
    if city:
        parts.append(city)
    state = (m.state or "").strip()
    if state and not _looks_like_state_zip(city):
        parts.append(state)
    zip_code = (m.zip_code or "").strip()
    if zip_code:
        parts.append(zip_code)
    return " ".join(p for p in parts if p).strip()


async def main() -> None:
    async with database.async_session() as db:
        members = (await db.execute(
            select(Member).where(Member.status.in_(("active", "recruit")))
        )).scalars().all()

        changes = []
        for m in members:
            addr = (m.address or "").strip()
            if not addr:
                continue
            city = (m.city or "").strip()
            zip_code = (m.zip_code or "").strip()
            bad_city = _looks_like_state_zip(city)
            if city and zip_code and not bad_city:
                continue  # already complete

            parsed = parse_oneline_address(_recombine(m))
            new_street = parsed["street"] or addr
            new_city = parsed["city"] or ("" if bad_city else city)

            # If the old city turned out to be part of the street (Bergener:
            # address='4315', city='Woodmeadow' -> street '4315 Woodmeadow'),
            # keep the repaired street and drop the duplicate rather than
            # storing a street name as the city.
            if (
                parsed["city"] is None
                and city
                and not bad_city
                and new_street.lower().endswith(city.lower())
            ):
                new_city = ""
            new_state = parsed["state"] or (m.state or "")
            new_zip = parsed["zip"] or zip_code

            diff = {}
            if new_street and new_street != addr:
                diff["address"] = (addr, new_street)
            if new_city != city:
                diff["city"] = (city, new_city)
            if new_state and new_state != (m.state or ""):
                diff["state"] = (m.state or "", new_state)
            if new_zip != zip_code:
                diff["zip_code"] = (zip_code, new_zip)
            if not diff:
                continue

            changes.append((m, diff))
            if APPLY:
                for field, (_old, new) in diff.items():
                    setattr(m, field, new)

        print(f"{'APPLYING' if APPLY else 'DRY RUN'} — {len(changes)} member(s) to repair\n")
        for m, diff in changes:
            print(f"  [{m.id}] {m.last_name}")
            for field, (old, new) in diff.items():
                print(f"       {field:9} {old!r}  ->  {new!r}")
        if not changes:
            print("  nothing to do")

        if APPLY and changes:
            await db.commit()
            print(f"\ncommitted {len(changes)} member(s)")
        elif changes:
            print("\n(dry run — re-run with --apply to write)")


asyncio.run(main())
