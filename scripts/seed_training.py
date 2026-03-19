#!/usr/bin/env python3
"""Seed TRADOC checklist items and certifications catalog."""

import os
import re
import psycopg2

DB_URL = os.environ.get("DATABASE_URL_SYNC")
if not DB_URL:
    import sys
    print("ERROR: DATABASE_URL_SYNC environment variable is not set.", file=sys.stderr)
    sys.exit(1)
m = re.match(r'postgresql\+psycopg2://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', DB_URL)
DB_PARAMS = {
    "user": m.group(1), "password": m.group(2),
    "host": m.group(3), "port": int(m.group(4)), "dbname": m.group(5),
}

TRADOC_ITEMS = [
    # (block, block_name, name, sort_order)
    # Block 1: Theory & Medical
    (1, "Theory & Medical", "Customs & Courtesies", 1),
    (1, "Theory & Medical", "Drill & Ceremony", 2),
    (1, "Theory & Medical", "Gear Review", 3),
    (1, "Theory & Medical", "Medical", 4),

    # Block 2: Weapons Qualification
    (2, "Weapons Qualification", "Weapons Familiarization", 5),
    (2, "Weapons Qualification", "Basic Rifle Marksmanship", 6),
    (2, "Weapons Qualification", "Rifle Qualification", 7),
    (2, "Weapons Qualification", "Shooting Drills", 8),
    (2, "Weapons Qualification", "Use of Force", 9),

    # Block 3: Supplemental Skills
    (3, "Supplemental Skills", "Radio Familiarity", 10),
    (3, "Supplemental Skills", "Net Etiquette", 11),
    (3, "Supplemental Skills", "Reports", 12),
    (3, "Supplemental Skills", "Convoy", 13),
    (3, "Supplemental Skills", "Basic Land Navigation", 14),
    (3, "Supplemental Skills", "Intermediate Land Navigation", 15),

    # Block 4: Combat Fundamentals
    (4, "Combat Fundamentals", "Conduct & React to Ambush", 16),
    (4, "Combat Fundamentals", "Hand & Arm Signals", 17),
    (4, "Combat Fundamentals", "Individual Movement Techniques", 18),
    (4, "Combat Fundamentals", "Patrolling", 19),
    (4, "Combat Fundamentals", "React to Contact", 20),
    (4, "Combat Fundamentals", "Recon 101", 21),

    # Block 0: Every FTX
    (0, "Every FTX", "FOB Setup & Security", 22),
    (0, "Every FTX", "Guard Duty", 23),
    (0, "Every FTX", "Stand-To", 24),
]

CERTIFICATIONS = [
    # (name, category, icon, sort_order)
    ("Sharpshooter", "marksmanship", "🎯", 1),
    ("Marksman", "marksmanship", "🔫", 2),
    ("GSAR 1", "search_rescue", "🔍", 3),
    ("GSAR 2", "search_rescue", "🔍", 4),
    ("GSAR 3", "search_rescue", "🔍", 5),
    ("Sabre", "leadership", "⚔️", 6),
    ("CERT", "specialty", "🛡️", 7),
    ("Rescue Boat Technician (RBT)", "search_rescue", "🚤", 8),
    ("FAST — Ops", "search_rescue", "🌊", 8),
    ("FAST 1", "search_rescue", "🌊", 9),
    ("FAST 2", "search_rescue", "🌊", 10),
    ("FAA Part 107 (Remote Pilot)", "specialty", "🛩️", 11),
]


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()

    # Seed TRADOC items
    print("=== TRADOC Checklist ===")
    for block, block_name, name, sort_order in TRADOC_ITEMS:
        cur.execute("""
            INSERT INTO tradoc_items (block, block_name, name, sort_order)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (block, block_name, name, sort_order))
        print(f"  BLK{block}: {name}")

    # Seed certifications
    print("\n=== Certifications ===")
    for name, category, icon, sort_order in CERTIFICATIONS:
        cur.execute("""
            INSERT INTO certifications (name, category, icon, sort_order)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
        """, (name, category, icon, sort_order))
        print(f"  {icon} {name} ({category})")

    conn.commit()
    cur.close()
    conn.close()
    print("\n✅ Seeded!")


if __name__ == "__main__":
    main()
