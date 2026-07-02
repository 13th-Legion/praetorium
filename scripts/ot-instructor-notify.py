#!/usr/bin/env python3
"""
Online Training instructor notifications for the 13th Legion.

Runs INSIDE the praetorium-app container (has DB access via app.database).

Modes:
  roster   -> Post the weekly "on deck / in the hole" roster to T2 Training,
              actively pinging next week's and the following week's instructors.
  reminder -> DM the NEXT upcoming instructor if the training is exactly
              3, 2, 1, or 0 days out. Self-adjusting to schedule swaps.

Data source: portal Postgres (events + members), queried live each run.
NC Talk delivery: OCS chat API as the 'spooky' service user (Basic auth).

Env:
  NC_HOST      default cloud.13thlegion.org
  NC_USER      default spooky
  NC_PASS      required
  T2_TRAINING_TOKEN default jf5p44k9
"""
import asyncio
import base64
import datetime as dt
import json
import os
import ssl
import sys
import urllib.parse
import http.client

from sqlalchemy import text
from app.database import async_session

NC_HOST = os.environ.get("NC_HOST", "cloud.13thlegion.org")
NC_USER = os.environ.get("NC_USER", "spooky")
NC_PASS = os.environ.get("NC_PASS", "")
T2_TRAINING = os.environ.get("T2_TRAINING_TOKEN", "jf5p44k9")
PORTAL_BASE = os.environ.get("PORTAL_BASE", "https://portal.13thlegion.org")


def event_url(ev):
    return f"{PORTAL_BASE}/events/{ev['id']}"

if not NC_PASS:
    raise SystemExit("[ot-notify] FATAL: NC_PASS not set")


def _auth():
    return base64.b64encode(f"{NC_USER}:{NC_PASS}".encode()).decode()


def _req(method, path, body=None):
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(NC_HOST, timeout=15, context=ctx)
    headers = {
        "Authorization": f"Basic {_auth()}",
        "OCS-APIRequest": "true",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(body).encode()
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode()
    conn.close()
    return resp.status, raw


def post_to_room(token, message):
    status, raw = _req(
        "POST",
        f"/ocs/v2.php/apps/spreed/api/v1/chat/{token}",
        {"message": message},
    )
    print(f"[ot-notify] post to {token}: HTTP {status}")
    if status not in (200, 201):
        print(f"[ot-notify]   body: {raw[:300]}")
    return status in (200, 201)


def get_or_create_dm(nc_username):
    """Return the 1:1 room token between spooky and nc_username, creating it if needed."""
    # roomType 1 = one-to-one; invite = target user id
    status, raw = _req(
        "POST",
        "/ocs/v2.php/apps/spreed/api/v4/room",
        {"roomType": 1, "invite": nc_username},
    )
    try:
        data = json.loads(raw)
        token = data["ocs"]["data"]["token"]
        print(f"[ot-notify] DM room for {nc_username}: {token} (HTTP {status})")
        return token
    except Exception:
        print(f"[ot-notify] FAILED to get/create DM for {nc_username}: HTTP {status} {raw[:300]}")
        return None


def mention(nc_username):
    return f'@"{nc_username}"'


def fmt_date(d):
    return d.strftime("%a %d %b").replace(" 0", " ")


async def upcoming_trainings(limit=6):
    """Return list of dicts for upcoming Online Training events, soonest first."""
    async with async_session() as s:
        r = await s.execute(text("""
            SELECT e.id, e.date_start, e.instructor_id,
                   m.callsign, m.rank_grade, m.first_name, m.last_name, m.nc_username
            FROM events e
            LEFT JOIN members m ON e.instructor_id = m.id
            WHERE e.title ILIKE '%Online Training%'
              AND e.date_start >= (NOW() AT TIME ZONE 'America/Chicago') - INTERVAL '2 hours'
            ORDER BY e.date_start
            LIMIT :lim
        """), {"lim": limit})
        rows = []
        for row in r:
            rows.append({
                "id": row[0],
                "date_start": row[1],
                "instructor_id": row[2],
                "callsign": row[3],
                "rank_grade": row[4],
                "first_name": row[5],
                "last_name": row[6],
                "nc_username": row[7],
            })
        return rows


def person_label(ev):
    rank = ev.get("rank_grade") or ""
    name = " ".join(x for x in [ev.get("first_name"), ev.get("last_name")] if x)
    cs = ev.get("callsign")
    base = name or "TBD"
    if cs:
        base = f"{base} ({cs})"
    return base.strip()


async def mode_roster():
    evs = await upcoming_trainings(limit=4)
    if not evs:
        print("[ot-notify] roster: no upcoming trainings found")
        return
    on_deck = evs[0]
    in_hole = evs[1] if len(evs) > 1 else None

    lines = ["📅 **Online Training — Instructor Rotation**", ""]

    # On deck
    if on_deck["nc_username"]:
        lines.append(
            f"🎯 **ON DECK** — {fmt_date(on_deck['date_start'])} @ 2000 CT: "
            f"{mention(on_deck['nc_username'])} ({person_label(on_deck)})"
        )
    else:
        lines.append(
            f"🎯 **ON DECK** — {fmt_date(on_deck['date_start'])} @ 2000 CT: "
            f"⚠️ NO INSTRUCTOR ASSIGNED"
        )
    lines.append(f"   → Event: {event_url(on_deck)}")

    # In the hole
    if in_hole:
        if in_hole["nc_username"]:
            lines.append(
                f"🕳️ **IN THE HOLE** — {fmt_date(in_hole['date_start'])} @ 2000 CT: "
                f"{mention(in_hole['nc_username'])} ({person_label(in_hole)})"
            )
        else:
            lines.append(
                f"🕳️ **IN THE HOLE** — {fmt_date(in_hole['date_start'])} @ 2000 CT: "
                f"⚠️ NO INSTRUCTOR ASSIGNED"
            )
        lines.append(f"   → Event: {event_url(in_hole)}")

    lines += [
        "",
        "If you're up, block the time now — and open your event link to add the topic(s) you'll cover.",
        "You'll get personal DM reminders 3, 2, and 1 days out plus day-of.",
        "Can't make your slot? Sort a swap with the S3 shop ASAP.",
    ]
    post_to_room(T2_TRAINING, "\n".join(lines))


async def mode_reminder():
    evs = await upcoming_trainings(limit=1)
    if not evs:
        print("[ot-notify] reminder: no upcoming trainings")
        return
    ev = evs[0]
    if not ev["nc_username"]:
        print(f"[ot-notify] reminder: next training (event {ev['id']}) has NO instructor assigned — skipping DM")
        return

    # Compute whole-day delta in Chicago local time.
    # date_start is naive local (America/Chicago) per portal convention.
    try:
        from zoneinfo import ZoneInfo
        today = dt.datetime.now(ZoneInfo("America/Chicago")).date()
    except Exception:
        # Fallback: assume CDT (-5). Day-granularity only.
        today = (dt.datetime.utcnow() - dt.timedelta(hours=5)).date()
    start = ev["date_start"]
    days_out = (start.date() - today).days

    if days_out not in (3, 2, 1, 0):
        print(f"[ot-notify] reminder: next training is {days_out} days out — no reminder today")
        return

    when = {
        3: "in **3 days**",
        2: "in **2 days**",
        1: "**tomorrow**",
        0: "**TODAY**",
    }[days_out]

    token = get_or_create_dm(ev["nc_username"])
    if not token:
        print("[ot-notify] reminder: could not obtain DM room — aborting")
        return

    label = person_label(ev)
    msg = (
        f"👋 Reminder: you're the **Online Training instructor {when}** — "
        f"{fmt_date(ev['date_start'])} @ 2000 CT, in **T2 · Training**.\n\n"
        f"📝 Open your event and add the topic(s) you'll cover:\n{event_url(ev)}\n\n"
        f"{'Tonight! ' if days_out == 0 else ''}"
        f"Have your material ready. If something's come up and you can't run it, "
        f"tell the S3 shop NOW so we can arrange a swap — don't leave the slot empty."
    )
    ok = post_to_room(token, msg)
    print(f"[ot-notify] reminder to {ev['nc_username']} ({label}), {days_out}d out: {'sent' if ok else 'FAILED'}")


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "roster"
    if mode == "roster":
        await mode_roster()
    elif mode == "reminder":
        await mode_reminder()
    else:
        raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    asyncio.run(main())
