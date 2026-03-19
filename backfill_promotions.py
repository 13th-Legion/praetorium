#!/usr/bin/env python3
"""Backfill rank history from historical promotion documents."""

import asyncio
import sys
import os
from datetime import datetime, date
from dataclasses import dataclass
from typing import List, Optional, Dict

# Add the portal app to the path
sys.path.insert(0, '/home/lkavadas/clawd/projects/praetorium/portal')

from app.database import async_session, engine
from app.models.member import Member
from app.models.rank_history import RankHistory
from sqlalchemy import select


@dataclass
class PromotionRecord:
    name: str
    callsign: Optional[str]
    old_rank: str
    new_rank: str
    effective_date: date
    source: str


def parse_promotions() -> List[PromotionRecord]:
    """Parse all the promotion data from documents."""
    promotions = []
    
    # February 2026
    promotions.extend([
        PromotionRecord("Bartlett", "Arvo", "PFC", "CPL", date(2026, 2, 1), "202602_proms_pats"),
        PromotionRecord("Wall", "Blackout", "CPL", "SGT", date(2026, 2, 1), "202602_proms_pats"),
    ])
    
    # January 2026
    promotions.extend([
        PromotionRecord("Harris", "Antman", "PVT", "PFC", date(2026, 1, 1), "202601_proms_pats"),
        PromotionRecord("Turner", "Skydog", "PVT", "PFC", date(2026, 1, 1), "202601_proms_pats"),
        PromotionRecord("Standlee", "Harpoon", "CPL", "SGT", date(2026, 1, 1), "202601_proms_pats"),
    ])
    
    # December 2025
    promotions.extend([
        PromotionRecord("Bunker", "Digger", "CPL", "SGT", date(2025, 12, 1), "202512_proms_pats"),
        PromotionRecord("Camp", "Kenobi", "SGT", "1SG", date(2025, 12, 1), "202512_proms_pats"),
    ])
    
    # November 2025
    promotions.extend([
        PromotionRecord("Moreno", "Hound", "CPL", "SGT", date(2025, 11, 1), "202511_proms_pats"),
        PromotionRecord("Richardson", "Spanky", "CPL", "SGT", date(2025, 11, 1), "202511_proms_pats"),
    ])
    
    # September 2025
    promotions.extend([
        PromotionRecord("Cyr", "Bromigo", "PFC", "CPL", date(2025, 9, 1), "202509_proms_pats"),
        PromotionRecord("Pati", None, "PFC", "CPL", date(2025, 9, 1), "202509_proms_pats"),
        PromotionRecord("Canzanella", "Blue Diver", "CPL", "SGT", date(2025, 9, 1), "202509_proms_pats"),
        PromotionRecord("Richardson", "Spanky", "CPL", "SGT", date(2025, 9, 1), "202509_proms_pats"),
    ])
    
    # May 2025 - Big promotion ceremony
    promotions.extend([
        PromotionRecord("Bartlett", "Arvo", "PVT", "PFC", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Hassell", None, "PVT", "PFC", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Bunker", "Digger", "PVT", "CPL", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Standlee", "Harpoon", "PVT", "CPL", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Richardson", "Spanky", "PVT", "CPL", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Wall", "Blackout", "PFC", "CPL", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Camp", "Kenobi", "CPL", "SGT", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Deaton", "Crash", "CPL", "SGT", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Gonzalez", "Romeo", "CPL", "SGT", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Ricci", "Sandman", "CPL", "SGT", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Henriques", "Johnny", "SGT", "SSG", date(2025, 5, 1), "202505_proms_pats"),
        PromotionRecord("Moreno", "Hound", "PVT", "CPL", date(2025, 5, 1), "202505_proms_pats"),
    ])
    
    # March 2025
    promotions.extend([
        PromotionRecord("Bunker", None, "PVT", "PFC", date(2025, 3, 1), "202503_proms_pats"),
        PromotionRecord("Standlee", None, "PVT", "PFC", date(2025, 3, 1), "202503_proms_pats"),
        PromotionRecord("Richardson", None, "PVT", "CPL", date(2025, 3, 1), "202503_proms_pats"),
    ])
    
    # February 2025
    promotions.extend([
        PromotionRecord("Bunker", "Digger", "PVT", "PFC", date(2025, 2, 7), "20250207_proms_pats"),
        PromotionRecord("Cyr", "Bromigo", "PVT", "PFC", date(2025, 2, 7), "20250207_proms_pats"),
        PromotionRecord("Standlee", "Harpoon", "PVT", "PFC", date(2025, 2, 7), "20250207_proms_pats"),
        PromotionRecord("Wall", "Blackout", "PVT", "PFC", date(2025, 2, 7), "20250207_proms_pats"),
        PromotionRecord("Canzanella", "Blue Diver", "PFC", "CPL", date(2025, 2, 7), "20250207_proms_pats"),
    ])
    
    # From Personnel Roster - June 2024 big promotion (founding leadership)
    promotions.extend([
        PromotionRecord("Wellman", "Tatanka", None, "CPT", date(2024, 6, 1), "PersonnelRoster"),
        PromotionRecord("Kavadas", "Cav", None, "1LT", date(2024, 6, 1), "PersonnelRoster"),
        PromotionRecord("Eastman", "Dizz", None, "SFC", date(2024, 6, 1), "PersonnelRoster"),
        PromotionRecord("Henriques", "Johnny", None, "SSG", date(2024, 6, 1), "PersonnelRoster"),
        PromotionRecord("Gonzalez", "Romeo", None, "SGT", date(2024, 6, 1), "PersonnelRoster"),
    ])
    
    # Additional roster dates
    promotions.extend([
        PromotionRecord("Locy", "Archer", None, "WO1", date(2025, 4, 1), "PersonnelRoster"),
        PromotionRecord("Canzanella", "Blue Diver", None, "SGT", date(2025, 2, 1), "PersonnelRoster"),
        PromotionRecord("Deaton", "Crash", None, "SGT", date(2024, 11, 1), "PersonnelRoster"),
        PromotionRecord("Pope", None, None, "PFC", date(2025, 1, 1), "PersonnelRoster"),
    ])
    
    return promotions


def normalize_rank(rank: str) -> str:
    """Convert various rank formats to standard E-X format."""
    rank_map = {
        "RCT": "E-1", "PVT": "E-2", "PFC": "E-3", "CPL": "E-4", 
        "SGT": "E-5", "SSG": "E-6", "SFC": "E-7", "1SG": "E-8", "SGM": "E-9",
        "WO1": "W-1", "2LT": "O-1", "1LT": "O-2", "CPT": "O-3", "MAJ": "O-4"
    }
    return rank_map.get(rank, rank)


async def find_member_by_name_or_callsign(session, name: str, callsign: Optional[str]) -> Optional[Member]:
    """Find member by last name or callsign."""
    # Try by callsign first (more specific)
    if callsign:
        result = await session.execute(
            select(Member).where(Member.callsign.ilike(callsign))
        )
        member = result.scalar_one_or_none()
        if member:
            return member
    
    # Try exact last name match
    result = await session.execute(
        select(Member).where(Member.last_name.ilike(name))
    )
    members = result.scalars().all()
    
    if len(members) == 1:
        return members[0]
    elif len(members) > 1:
        print(f"⚠️  Multiple members found for '{name}': {[m.display_name for m in members]}")
        return None
    
    return None


async def backfill_promotions():
    """Backfill all historical promotions."""
    async with async_session() as session:
        promotions = parse_promotions()
        inserted = 0
        skipped = 0
        
        print(f"Processing {len(promotions)} promotion records...")
        
        for promo in promotions:
            # Find the member
            member = await find_member_by_name_or_callsign(session, promo.name, promo.callsign)
            
            if not member:
                print(f"⚠️  Member not found: {promo.name} ({promo.callsign})")
                skipped += 1
                continue
            
            # Check if this promotion already exists (same member, rank, and month)
            new_rank_norm = normalize_rank(promo.new_rank) if promo.new_rank else None
            
            existing = await session.execute(
                select(RankHistory)
                .where(RankHistory.member_id == member.id)
                .where(RankHistory.new_rank == new_rank_norm)
            )
            
            if existing.scalar_one_or_none():
                print(f"⚠️  Promotion already exists: {member.display_name} → {promo.new_rank} on {promo.effective_date}")
                skipped += 1
                continue
            
            # Create the promotion record
            old_rank_norm = normalize_rank(promo.old_rank) if promo.old_rank else None
            
            rank_history = RankHistory(
                member_id=member.id,
                old_rank=old_rank_norm,
                new_rank=new_rank_norm,
                changed_by="system_backfill",
                notes=f"Backfilled from {promo.source}",
                effective_date=datetime.combine(promo.effective_date, datetime.min.time()),
            )
            
            session.add(rank_history)
            print(f"✅ Added: {member.display_name} {promo.old_rank}→{promo.new_rank} on {promo.effective_date}")
            inserted += 1
        
        await session.commit()
        print(f"\n✅ Backfill complete: {inserted} promotions added, {skipped} skipped")


if __name__ == "__main__":
    asyncio.run(backfill_promotions())