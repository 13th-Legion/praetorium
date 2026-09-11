#!/usr/bin/env python3
"""Reconcile member email addresses between the roster and Nextcloud.

A member's email lives in two systems:

  * ``members.email`` in the portal DB — the roster, source of truth for PII,
    used by Praetorium's own mail (WARNO/OPORD/FRAGO, credentials).
  * the **Nextcloud account** address — used by Nextcloud's own mail:
    notification digests, password-change notices, share notifications.

``app/routes/member_edit`` now pushes the roster address to Nextcloud on every
edit (via ``app.services.nc_users.set_email``), so new drift should not appear.
This script exists for the two cases that hook cannot cover:

  1. historical drift accumulated before the hook existed;
  2. changes made directly in Nextcloud, or a push that failed while NC was
     down (the hook is best-effort by design — it must never 500 a portal save).

Comparison is **case-insensitive**. The audit that prompted this found 17
"mismatches" of which 11 were pure capitalisation; treating those as drift
produces permanent false alarms that bury the real ones.

Run inside the app container, which already has DATABASE_URL and the NC
service credentials:

    docker exec praetorium-app sh -c \\
      'cd /app && PYTHONPATH=/app python3 scripts/reconcile-member-emails.py'

Dry run by default — it changes nothing without ``--apply``.

    --apply            actually write the roster address to Nextcloud
    --include-case     also treat capitalisation-only differences as drift
    --user USERNAME    limit to one nc_username (repeatable)
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx
from sqlalchemy import select

from app import database
from app.models.member import Member
from app.services import nc_users


def _norm(s):
    return (s or "").strip().lower()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the roster address to Nextcloud (default: dry run)")
    ap.add_argument("--include-case", action="store_true",
                    help="also report capitalisation-only differences")
    ap.add_argument("--user", action="append", default=[],
                    help="limit to this nc_username (repeatable)")
    args = ap.parse_args()

    async with database.async_session() as db:
        q = select(Member).where(
            Member.nc_username.isnot(None),
            Member.nc_username != "",
            Member.status.in_(["active", "recruit"]),
        ).order_by(Member.nc_username)
        members = (await db.execute(q)).scalars().all()

    if args.user:
        wanted = {u.lower() for u in args.user}
        members = [m for m in members if (m.nc_username or "").lower() in wanted]

    print(f"checking {len(members)} member(s); mode="
          f"{'APPLY' if args.apply else 'DRY RUN'}, "
          f"case-only differences {'INCLUDED' if args.include_case else 'ignored'}")
    print()

    real, case_only, unreadable, fixed, failed = [], [], [], [], []

    async with httpx.AsyncClient(timeout=20) as client:
        for m in members:
            roster = (m.email or "").strip()
            if not roster:
                continue
            nc = await nc_users.get_email(m.nc_username, client=client)
            if nc is None:
                unreadable.append((m.nc_username, roster))
                continue
            nc = nc.strip()
            if roster == nc:
                continue

            differs = nc_users.emails_differ(roster, nc)
            if not differs and not args.include_case:
                case_only.append((m.nc_username, roster, nc))
                continue

            bucket = real if differs else case_only
            bucket.append((m.nc_username, roster, nc))

            if args.apply:
                ok, detail = await nc_users.set_email(m.nc_username, roster,
                                                      client=client)
                (fixed if ok else failed).append((m.nc_username, roster, detail))

    def _show(title, rows, cols=3):
        if not rows:
            return
        print(f"--- {title} ({len(rows)}) ---")
        for r in rows:
            if cols == 3:
                print(f"  {r[0]:<24} roster={r[1]:<36} nextcloud={r[2]}")
            else:
                print(f"  {r[0]:<24} {r[1]}")
        print()

    _show("FUNCTIONAL DRIFT — different mailbox", real)
    _show("capitalisation only — same mailbox, ignored unless --include-case",
          case_only)
    _show("could not read the Nextcloud address", unreadable, cols=2)

    if args.apply:
        if fixed:
            print(f"--- UPDATED IN NEXTCLOUD ({len(fixed)}) ---")
            for u, e, _ in fixed:
                print(f"  {u:<24} -> {e}")
            print()
        if failed:
            print(f"--- FAILED ({len(failed)}) ---")
            for u, e, d in failed:
                print(f"  {u:<24} -> {e}    {d}")
            print()

    print(f"summary: {len(real)} functional drift, {len(case_only)} case-only, "
          f"{len(unreadable)} unreadable"
          + (f", {len(fixed)} fixed, {len(failed)} failed" if args.apply else ""))

    if not args.apply and real:
        print()
        print("⚠️  Functional drift is NOT auto-fixed by a dry run, and should not be")
        print("   blindly applied either: where the two addresses are genuinely")
        print("   different mailboxes, a human has to say which one is correct.")
        print("   Re-run with --apply only once that is settled.")

    return 1 if (failed or unreadable) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
