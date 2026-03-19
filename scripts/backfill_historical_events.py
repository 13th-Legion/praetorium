#!/usr/bin/env python3
"""PP-074 — Historical FTX event + attendance backfill.

Idempotent: safe to run multiple times.
Run inside the app container:
  docker exec -it praetorium-app python3 /opt/praetorium/scripts/backfill_historical_events.py
"""

import json
import os
import sys
from datetime import datetime, date, timedelta
from collections import defaultdict

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Database connection (sync — this is a one-shot script)
# ---------------------------------------------------------------------------
DB_URL = os.environ.get("DATABASE_URL_SYNC")
if not DB_URL:
    import sys
    print("ERROR: DATABASE_URL_SYNC environment variable is not set.", file=sys.stderr)
    sys.exit(1)

engine = create_engine(DB_URL)

# ---------------------------------------------------------------------------
# Event type → category mapping
# ---------------------------------------------------------------------------
CATEGORY_MAP = {
    "FTX": "ftx",
    "MCFTX": "mcftx",
    "Family Day": "family_day",
    "Marksman Course": "external_training",
}

TITLE_MAP = {
    "FTX": "Field Training Exercise",
    "MCFTX": "Multi-Company Field Training Exercise",
    "Family Day": "Family Day",
    "Marksman Course": "Marksman Course",
}

# Categories that count toward ftx_count
FTX_CATEGORIES = {"ftx", "mcftx"}

# Categories that get TRADOC auto-credit
TRADOC_CATEGORIES = {"ftx", "mcftx"}

# Block 0 TRADOC item IDs (every FTX)
BLOCK_0_ITEMS = [19, 20, 21]

# Block-specific item IDs
BLOCK_ITEMS = {
    1: [1, 2, 3, 4],
    2: [5, 6, 7, 8, 9],
    3: [10, 11, 12],
    4: [13, 14, 15, 16, 17, 18],
}

# Family Day 2025 attendees (from Google Form)
FAMILY_DAY_2025_ATTENDEES = [
    ("Cyr", "Paul"),
    ("Kavadas", "Levi"),
    ("Locy", "Adam"),
    ("Deaton", "Russ"),
    ("Pati", "Jason"),
    ("Standlee", "Eric"),
    ("Turner", "Paul"),
    ("Moreno", "Albert"),
    ("Hutchison", "Jim"),
    ("Bunker", "Clint"),
    ("Chastain", "Everett"),
    ("Chavez", "Christian"),
    ("Canzanella", "James"),
    ("Hooper", "Asa"),
]

# Marksman Course completers (get cert_id=2)
MARKSMAN_COMPLETERS = ["Locy", "Gonzalez", "Bunker", "Ventimiglio"]

# MCFTX with no attendees
NO_ATTENDEES_EVENT = "4/14/24 - 4/17/24"  # Not in JSON — we add manually


def parse_date_range(date_range: str):
    """Parse date range string into (start_date, end_date) as datetime objects."""
    parts = date_range.split(" - ")
    start_str = parts[0].strip()

    # Parse start date
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            start = datetime.strptime(start_str, fmt)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"Cannot parse start date: {start_str}")

    if len(parts) == 2:
        end_str = parts[1].strip()
        for fmt in ("%m/%d/%y", "%m/%d/%Y"):
            try:
                end = datetime.strptime(end_str, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Cannot parse end date: {end_str}")
    else:
        end = start

    return start, end


def main():
    # Load backfill JSON
    json_path = os.environ.get(
        "BACKFILL_JSON",
        "/opt/praetorium/ftx_attendance_backfill.json",
    )
    if not os.path.exists(json_path):
        # Try local dev path
        json_path = os.path.join(os.path.dirname(__file__), "..", "..", "ftx_attendance_backfill.json")
    
    with open(json_path) as f:
        data = json.load(f)

    events_data = data["events"]
    attendance_data = data["attendance"]

    with Session(engine) as db:
        # ------------------------------------------------------------------
        # Load all members by last_name + first_name
        # ------------------------------------------------------------------
        rows = db.execute(text("SELECT id, first_name, last_name, status FROM members")).fetchall()
        member_map = {}  # (last_name_lower, first_name_lower) -> member_id
        member_status = {}  # member_id -> status
        for mid, fname, lname, status in rows:
            key = (lname.lower().strip(), fname.lower().strip())
            # Prefer active/recruit over inactive
            if key not in member_map or status in ("active", "recruit"):
                member_map[key] = mid
                member_status[mid] = status

        print(f"Loaded {len(member_map)} unique members from DB")

        # ------------------------------------------------------------------
        # Step 1: Create Event records
        # ------------------------------------------------------------------
        # Build event list from JSON + manual additions
        all_events = []
        for ev in events_data:
            start, end = parse_date_range(ev["date_range"])
            category = CATEGORY_MAP.get(ev["event_type"], "other")
            title = TITLE_MAP.get(ev["event_type"], ev["event_type"])
            tb = int(ev["training_block"]) if ev["training_block"] else None
            all_events.append({
                "title": title,
                "category": category,
                "date_start": start,
                "date_end": end,
                "training_block": tb,
                "col_index": ev["col_index"],
                "event_type": ev["event_type"],
            })

        # Add MCFTX Apr 14-17, 2024 (no attendees)
        all_events.append({
            "title": "Multi-Company Field Training Exercise",
            "category": "mcftx",
            "date_start": datetime(2024, 4, 14),
            "date_end": datetime(2024, 4, 17),
            "training_block": None,
            "col_index": -1,
            "event_type": "MCFTX",
        })

        # Sort by date
        all_events.sort(key=lambda e: e["date_start"])

        # Create events in DB (idempotent — check by date_start + category)
        event_id_map = {}  # col_index -> event_id
        events_created = 0
        events_skipped = 0

        for ev in all_events:
            existing = db.execute(
                text("""
                    SELECT id FROM events 
                    WHERE date(date_start) = :ds AND category = :cat
                """),
                {"ds": ev["date_start"].date(), "cat": ev["category"]},
            ).fetchone()

            if existing:
                event_id_map[ev["col_index"]] = existing[0]
                events_skipped += 1
                # Update finalization fields if not already set
                db.execute(
                    text("""
                        UPDATE events SET 
                            status = 'complete',
                            finalized_at = COALESCE(finalized_at, NOW()),
                            finalized_by = COALESCE(finalized_by, 'backfill'),
                            training_block = COALESCE(training_block, :tb),
                            updated_at = NOW()
                        WHERE id = :eid
                    """),
                    {"eid": existing[0], "tb": ev["training_block"]},
                )
            else:
                result = db.execute(
                    text("""
                        INSERT INTO events (title, category, date_start, date_end, status,
                            training_block, rsvp_enabled, created_by, created_at, updated_at,
                            finalized_at, finalized_by)
                        VALUES (:title, :cat, :ds, :de, 'complete', :tb, false, 'backfill',
                            NOW(), NOW(), NOW(), 'backfill')
                        RETURNING id
                    """),
                    {
                        "title": ev["title"],
                        "cat": ev["category"],
                        "ds": ev["date_start"],
                        "de": ev["date_end"],
                        "tb": ev["training_block"],
                    },
                )
                new_id = result.fetchone()[0]
                event_id_map[ev["col_index"]] = new_id
                events_created += 1

        db.commit()
        print(f"Events: {events_created} created, {events_skipped} already existed")

        # Also build a date->event_id lookup for Family Day 2025
        # Family Day 2025 = col_index 10 (08/09/25)
        family_day_event_id = event_id_map.get(10)
        marksman_event_id = event_id_map.get(16)  # col_index 16 = 2/21/25-2/23/25

        # ------------------------------------------------------------------
        # Step 2: Create EventRSVP records from attendance JSON
        # ------------------------------------------------------------------
        rsvps_created = 0
        rsvps_skipped = 0
        unmatched = []

        # Build event_index -> col_index mapping
        # events_data[i] has col_index, attendance events_attended[j] has event_index
        idx_to_col = {i: events_data[i]["col_index"] for i in range(len(events_data))}

        for person in attendance_data:
            key = (person["last_name"].lower().strip(), person["first_name"].lower().strip())
            member_id = member_map.get(key)
            if not member_id:
                if person["events_attended"]:
                    unmatched.append(f"{person['first_name']} {person['last_name']}")
                continue

            for att in person["events_attended"]:
                event_idx = att["event_index"]
                col_index = idx_to_col.get(event_idx)
                if col_index is None:
                    continue
                event_id = event_id_map.get(col_index)
                if event_id is None:
                    continue

                # Check if RSVP already exists
                existing = db.execute(
                    text("SELECT id FROM event_rsvps WHERE event_id = :eid AND member_id = :mid"),
                    {"eid": event_id, "mid": member_id},
                ).fetchone()

                if existing:
                    # Ensure attended=True
                    db.execute(
                        text("UPDATE event_rsvps SET attended = true, status = 'attending' WHERE id = :rid"),
                        {"rid": existing[0]},
                    )
                    rsvps_skipped += 1
                else:
                    db.execute(
                        text("""
                            INSERT INTO event_rsvps (event_id, member_id, status, attended,
                                responded_at, created_at, updated_at, checked_in, auto_declined)
                            VALUES (:eid, :mid, 'attending', true, NOW(), NOW(), NOW(), false, false)
                        """),
                        {"eid": event_id, "mid": member_id},
                    )
                    rsvps_created += 1

        db.commit()
        print(f"RSVPs from JSON: {rsvps_created} created, {rsvps_skipped} updated/existed")
        if unmatched:
            print(f"  ⚠ Unmatched members: {', '.join(unmatched)}")

        # ------------------------------------------------------------------
        # Step 3: Family Day 2025 attendees
        # ------------------------------------------------------------------
        if family_day_event_id:
            fd_created = 0
            for lname, fname in FAMILY_DAY_2025_ATTENDEES:
                key = (lname.lower(), fname.lower())
                member_id = member_map.get(key)
                if not member_id:
                    print(f"  ⚠ Family Day: no match for {fname} {lname}")
                    continue

                existing = db.execute(
                    text("SELECT id FROM event_rsvps WHERE event_id = :eid AND member_id = :mid"),
                    {"eid": family_day_event_id, "mid": member_id},
                ).fetchone()

                if not existing:
                    db.execute(
                        text("""
                            INSERT INTO event_rsvps (event_id, member_id, status, attended,
                                responded_at, created_at, updated_at, checked_in, auto_declined)
                            VALUES (:eid, :mid, 'attending', true, NOW(), NOW(), NOW(), false, false)
                        """),
                        {"eid": family_day_event_id, "mid": member_id},
                    )
                    fd_created += 1
                else:
                    db.execute(
                        text("UPDATE event_rsvps SET attended = true, status = 'attending' WHERE id = :rid"),
                        {"rid": existing[0]},
                    )

            db.commit()
            print(f"Family Day 2025: {fd_created} RSVPs created")
        else:
            print("⚠ Family Day 2025 event not found in map")

        # ------------------------------------------------------------------
        # Step 4: Marksman Course certifications
        # ------------------------------------------------------------------
        if marksman_event_id:
            mc_certs = 0
            # All Marksman Course attendees are already in the JSON attendance data
            # (Locy, Gonzalez, Bunker, Ventimiglio, Wall)
            # Wall is already in the JSON as event_index 12

            for last_name in MARKSMAN_COMPLETERS:
                # Find member by last name
                mid = None
                for (ln, fn), m_id in member_map.items():
                    if ln == last_name.lower():
                        mid = m_id
                        break
                if not mid:
                    print(f"  ⚠ Marksman cert: no match for {last_name}")
                    continue

                # Check if cert already exists
                existing = db.execute(
                    text("SELECT id FROM member_certifications WHERE member_id = :mid AND certification_id = 2"),
                    {"mid": mid},
                ).fetchone()

                if not existing:
                    db.execute(
                        text("""
                            INSERT INTO member_certifications (member_id, certification_id, awarded_by, awarded_at, notes)
                            VALUES (:mid, 2, 'backfill', :dt, 'Marksman Course Feb 2025')
                        """),
                        {"mid": mid, "dt": datetime(2025, 2, 23)},
                    )
                    mc_certs += 1

            db.commit()
            print(f"Marksman certifications: {mc_certs} awarded")
        else:
            print("⚠ Marksman Course event not found in map")

        # ------------------------------------------------------------------
        # Step 5: TRADOC auto-credit for FTX/MCFTX events
        # ------------------------------------------------------------------
        tradoc_created = 0
        tradoc_skipped = 0

        # Get all events that should get TRADOC credits
        event_rows = db.execute(
            text("""
                SELECT id, category, training_block, date_start, title 
                FROM events 
                WHERE category IN ('ftx', 'mcftx') AND finalized_by = 'backfill'
            """)
        ).fetchall()

        # Get all existing member_tradoc records for fast lookup
        existing_tradoc = set()
        for row in db.execute(text("SELECT member_id, item_id FROM member_tradoc")).fetchall():
            existing_tradoc.add((row[0], row[1]))

        for event_id_val, category, training_block, date_start, title in event_rows:
            # Determine which TRADOC items to credit
            items_to_credit = list(BLOCK_0_ITEMS)
            if training_block and training_block in BLOCK_ITEMS:
                items_to_credit.extend(BLOCK_ITEMS[training_block])

            # Get attendees for this event
            attendees = db.execute(
                text("SELECT member_id FROM event_rsvps WHERE event_id = :eid AND attended = true"),
                {"eid": event_id_val},
            ).fetchall()

            for (member_id,) in attendees:
                for item_id in items_to_credit:
                    if (member_id, item_id) in existing_tradoc:
                        tradoc_skipped += 1
                        continue

                    db.execute(
                        text("""
                            INSERT INTO member_tradoc (member_id, item_id, signed_off_by, signed_off_at, ftx_date, notes)
                            VALUES (:mid, :iid, 'backfill', NOW(), :fd, :notes)
                        """),
                        {
                            "mid": member_id,
                            "iid": item_id,
                            "fd": date_start.date() if hasattr(date_start, 'date') else date_start,
                            "notes": f"Auto-credited: {title}",
                        },
                    )
                    existing_tradoc.add((member_id, item_id))
                    tradoc_created += 1

        db.commit()
        print(f"TRADOC credits: {tradoc_created} created, {tradoc_skipped} already existed")

        # ------------------------------------------------------------------
        # Step 6: Update members.ftx_count and members.last_ftx
        # ------------------------------------------------------------------
        # Count attended FTX/MCFTX events per member
        ftx_stats = db.execute(
            text("""
                SELECT r.member_id, COUNT(*) as cnt, MAX(e.date_start) as latest
                FROM event_rsvps r
                JOIN events e ON e.id = r.event_id
                WHERE r.attended = true AND e.category IN ('ftx', 'mcftx')
                GROUP BY r.member_id
            """)
        ).fetchall()

        updated_count = 0
        for member_id, cnt, latest in ftx_stats:
            latest_date = latest.date() if hasattr(latest, 'date') else latest
            db.execute(
                text("""
                    UPDATE members SET ftx_count = :cnt, last_ftx = :last, updated_at = NOW()
                    WHERE id = :mid
                """),
                {"cnt": cnt, "last": latest_date, "mid": member_id},
            )
            updated_count += 1

        db.commit()
        print(f"Updated ftx_count/last_ftx for {updated_count} members")

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        print("\n✅ Backfill complete!")
        print(f"  Events: {events_created + events_skipped} total ({events_created} new)")
        print(f"  RSVPs: {rsvps_created} new attendance records")
        print(f"  TRADOC: {tradoc_created} new sign-offs")
        print(f"  Members: {updated_count} ftx_count updated")


if __name__ == "__main__":
    main()
