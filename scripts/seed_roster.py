#!/usr/bin/env python3
"""Seed the members table from the Google Sheets roster CSV export.

Usage (inside container):
    python scripts/seed_roster.py /path/to/roster.csv

Or from host:
    docker exec -e PYTHONPATH=/app -e DATABASE_URL_SYNC=... praetorium-app python scripts/seed_roster.py /data/roster.csv
"""

import csv
import sys
import os
import re
from datetime import datetime, date

import psycopg2

DB_URL = os.environ.get("DATABASE_URL_SYNC")
if not DB_URL:
    print("ERROR: DATABASE_URL_SYNC environment variable is not set.", file=sys.stderr)
    sys.exit(1)

# Parse psycopg2 connection params from SQLAlchemy URL
m = re.match(r'postgresql\+psycopg2://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', DB_URL)
DB_PARAMS = {
    "user": m.group(1),
    "password": m.group(2),
    "host": m.group(3),
    "port": int(m.group(4)),
    "dbname": m.group(5),
}

# Rank abbreviation → grade mapping
RANK_MAP = {
    "RCT": "E-1",
    "PVT": "E-2",
    "PV2": "E-2",
    "PFC": "E-3",
    "CPL": "E-4",
    "SGT": "E-5",
    "SSG": "E-6",
    "SFC": "E-7",
    "1SG": "E-8",
    "MSG": "E-8",
    "SGM": "E-9",
    "2LT": "O-1",
    "1LT": "O-2",
    "CPT": "O-3",
    "MAJ": "O-4",
    "WO1": "W-1",
}

# Team detection from section headers
TEAM_SECTIONS = {
    "HEADQUARTERS": "Headquarters",
    "ARROW": "Arrow",
    "BADGER": "Badger",
    "CHAOS": "Chaos",
    "DELTA": "Delta",
    "ECHO": "Echo",
    "FOXTROT": "Foxtrot",
    "WRAITH": "Wraith",
    "PHOENIX": "Phoenix",
}


def parse_date(s):
    """Parse dates like 'Oct 2019', 'Jun 2024', etc."""
    if not s or s.strip() == "" or s == "Dec 1899":
        return None
    try:
        return datetime.strptime(s.strip(), "%b %Y").date()
    except ValueError:
        return None


def parse_address(addr):
    """Parse address string into components."""
    if not addr or addr.strip() == "":
        return None, None, None, None
    
    addr = addr.strip().strip('"')
    
    # Try to extract city, state, zip from end
    # Pattern: "..., City, TX 75056" or "..., City, Texas 76028"
    m = re.search(r',\s*([^,]+?),?\s*(?:TX|Texas)\s*(\d{5})\s*$', addr, re.IGNORECASE)
    if m:
        city = m.group(1).strip()
        zip_code = m.group(2)
        street = addr[:m.start()].strip().rstrip(',')
        return street, city, "TX", zip_code
    
    return addr, None, "TX", None


def parse_ham(ham_str):
    """Extract HAM callsign and license class from strings like 'KI5VOW (T), WSAV441'"""
    if not ham_str or ham_str.strip() == "" or ham_str.strip() == "x":
        return None, None
    
    # Look for pattern like KI5VOW (T) or KJ5ERK (T) or KI5WEQ (G)
    m = re.search(r'([A-Z]{1,2}\d[A-Z]{1,3})\s*\(([TGE])\)', ham_str)
    if m:
        callsign = m.group(1)
        cls_map = {"T": "Technician", "G": "General", "E": "Extra"}
        return callsign, cls_map.get(m.group(2), m.group(2))
    
    return None, None


def parse_veteran(prior_service):
    """Check if prior service indicates veteran status."""
    if not prior_service or prior_service.strip() == "":
        return False, None
    return True, prior_service.strip()


def guess_nc_username(first, last):
    """Generate likely NC username: first.last lowercase."""
    if not first or not last:
        return None
    return f"{first.strip().lower()}.{last.strip().lower()}"


def determine_status(rank_abbr):
    """Determine member status from rank."""
    if rank_abbr == "RCT":
        return "recruit"
    return "active"


def main(csv_path):
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    
    current_team = None
    imported = 0
    skipped = 0
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        
        for row in reader:
            if not row or len(row) < 2:
                continue
            
            # Check for section header (e.g., "HEADQUARTERS (9)")
            first_cell = row[0].strip()
            for key, team_name in TEAM_SECTIONS.items():
                if first_cell.startswith(key):
                    current_team = team_name
                    print(f"\n--- {team_name} ---")
                    break
            else:
                # Check if this is a data row (has a valid rank)
                rank_abbr = first_cell
                if rank_abbr not in RANK_MAP:
                    continue
                
                if not current_team:
                    continue
                
                # Parse row
                last_name = row[1].strip() if len(row) > 1 else ""
                first_name = row[2].strip() if len(row) > 2 else ""
                callsign = row[3].strip() if len(row) > 3 and row[3].strip() else None
                phone = row[4].strip() if len(row) > 4 and row[4].strip() else None
                email = row[5].strip() if len(row) > 5 and row[5].strip() else None
                address_raw = row[6].strip() if len(row) > 6 else ""
                entry_date = parse_date(row[7] if len(row) > 7 else "")
                promo_date = parse_date(row[8] if len(row) > 8 else "")
                ec_name = row[9].strip() if len(row) > 9 and row[9].strip() else None
                ec_number = row[10].strip() if len(row) > 10 and row[10].strip() else None
                prior_service = row[11] if len(row) > 11 else ""
                ham_raw = row[12] if len(row) > 12 else ""
                discord = row[13].strip() if len(row) > 13 and row[13].strip() else None
                shop = row[14].strip() if len(row) > 14 and row[14].strip() else None
                
                rank_grade = RANK_MAP[rank_abbr]
                status = determine_status(rank_abbr)
                street, city, state, zip_code = parse_address(address_raw)
                ham_callsign, ham_class = parse_ham(ham_raw)
                is_veteran, mos = parse_veteran(prior_service)
                nc_username = guess_nc_username(first_name, last_name)
                
                # Check for no media flag
                no_media = row[17].strip() if len(row) > 17 and row[17].strip() else None
                
                # Upsert by nc_username
                cur.execute("""
                    INSERT INTO members (
                        nc_username, first_name, last_name, callsign, email,
                        rank_grade, status, team, company,
                        primary_billet, join_date,
                        phone, address, city, state, zip_code,
                        ham_callsign, ham_license_class,
                        is_veteran, mos, has_ltc,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        NOW(), NOW()
                    )
                    ON CONFLICT (nc_username) DO UPDATE SET
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name,
                        callsign = EXCLUDED.callsign,
                        email = EXCLUDED.email,
                        rank_grade = EXCLUDED.rank_grade,
                        status = EXCLUDED.status,
                        team = EXCLUDED.team,
                        primary_billet = EXCLUDED.primary_billet,
                        join_date = EXCLUDED.join_date,
                        phone = EXCLUDED.phone,
                        address = EXCLUDED.address,
                        city = EXCLUDED.city,
                        state = EXCLUDED.state,
                        zip_code = EXCLUDED.zip_code,
                        ham_callsign = EXCLUDED.ham_callsign,
                        ham_license_class = EXCLUDED.ham_license_class,
                        is_veteran = EXCLUDED.is_veteran,
                        mos = EXCLUDED.mos,
                        updated_at = NOW()
                """, (
                    nc_username, first_name, last_name, callsign, email,
                    rank_grade, status, current_team, "13th Legion",
                    shop, entry_date,
                    phone, street, city, state, zip_code,
                    ham_callsign, ham_class,
                    is_veteran, mos, False,
                ))
                
                imported += 1
                print(f"  {rank_abbr} {last_name}, {first_name} ({callsign or '-'}) → {current_team} [{status}]")
    
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"\n✅ Imported {imported} members, skipped {skipped}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python seed_roster.py <path_to_csv>")
        sys.exit(1)
    main(sys.argv[1])
