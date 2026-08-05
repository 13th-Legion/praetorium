"""Reconcile Block 100 'Certifications' TRADOC items into the Certification table.

Block 100 was a TRADOC-block mirror of qualifications that are ALSO real
Certification records. This created duplicate sign-off surfaces and left the
profile "Advanced Training" section empty even when a member held the certs.

Single source of truth = Certification table.

This script:
  1. Ensures a Certification exists for every Block 100 item (creates the 4 that
     have no cert equivalent: Advanced Land Navigation, Expert Land Navigation,
     FRO, Tactical Comms).
  2. Migrates any existing Block 100 MemberTradoc sign-offs into MemberCertification
     awards on the matching cert (no lost credit, no dupes).
  3. Soft-deletes (archived=True) the Block 100 TradocItems so the Advanced
     Training display section disappears and the duplicate claim surface closes.

Idempotent. Run with --commit to write; default is dry-run.
"""
import asyncio
import sys
from sqlalchemy import select
from app.database import async_session
from app.models.training import (
    TradocItem, MemberTradoc, Certification, MemberCertification,
)

# Block 100 item name -> (target cert name, cert category, icon)
# Items whose cert already exists reuse the existing record (matched by name).
CERT_HOMES = {
    "Equites":                  ("Equites",                  "elite",         "🎖️"),
    "Marksman":                 ("Marksman",                 "marksmanship",  "🎯"),
    "Sharpshooter":             ("Sharpshooter",             "marksmanship",  "🎯"),
    "Sabre":                    ("Sabre",                    "search_rescue", "⚔️"),
    "Advanced Land Navigation": ("Advanced Land Navigation", "specialty",     "🧭"),
    "Expert Land Navigation":   ("Expert Land Navigation",   "specialty",     "🧭"),
    "FRO":                      ("FRO",                      "medical",       "🚑"),
    "Tactical Comms":           ("Tactical Comms",           "communications","📡"),
}


async def main(commit: bool):
    async with async_session() as db:
        b100 = (await db.execute(
            select(TradocItem).where(TradocItem.block == 100)
        )).scalars().all()
        if not b100:
            print("No Block 100 items found — nothing to do.")
            return

        all_certs = (await db.execute(select(Certification))).scalars().all()
        cert_by_name = {(c.name or "").strip().lower(): c for c in all_certs}

        item_to_cert = {}  # item_id -> Certification (may be pending flush)

        print("=== STEP 1: ensure a cert exists for every Block 100 item ===")
        for item in b100:
            nm = (item.name or "").strip()
            if nm not in CERT_HOMES:
                print(f"  !! item {item.id} '{nm}' not in CERT_HOMES map — SKIPPING (manual review)")
                continue
            cert_name, cat, icon = CERT_HOMES[nm]
            existing = cert_by_name.get(cert_name.strip().lower())
            if existing:
                print(f"  = item {item.id} '{nm}' -> existing cert '{cert_name}' (id {existing.id})")
                item_to_cert[item.id] = existing
            else:
                c = Certification(
                    name=cert_name, category=cat, icon=icon,
                    description=item.description or None, sort_order=item.sort_order or 0,
                )
                db.add(c)
                await db.flush()
                cert_by_name[cert_name.strip().lower()] = c
                item_to_cert[item.id] = c
                print(f"  + item {item.id} '{nm}' -> CREATED cert '{cert_name}' [{cat}] (id {c.id})")

        print("\n=== STEP 2: migrate Block 100 sign-offs -> cert awards ===")
        b100_ids = [i.id for i in b100]
        signoffs = (await db.execute(
            select(MemberTradoc).where(MemberTradoc.item_id.in_(b100_ids))
        )).scalars().all()
        print(f"  {len(signoffs)} Block 100 sign-off record(s) found")
        migrated = 0
        for so in signoffs:
            cert = item_to_cert.get(so.item_id)
            if not cert:
                print(f"  !! signoff member={so.member_id} item={so.item_id}: no target cert, SKIP")
                continue
            # dedupe: skip if member already holds this cert
            already = (await db.execute(
                select(MemberCertification).where(
                    MemberCertification.member_id == so.member_id,
                    MemberCertification.certification_id == cert.id,
                )
            )).scalar_one_or_none()
            if already:
                print(f"  = member {so.member_id} already holds cert '{cert.name}' — skip")
                continue
            db.add(MemberCertification(
                member_id=so.member_id,
                certification_id=cert.id,
                awarded_by=so.signed_off_by or "reconcile",
                awarded_at=so.signed_off_at,
                notes=f"Migrated from Block 100 TRADOC sign-off (item {so.item_id}). "
                      + (so.notes or ""),
            ))
            migrated += 1
            print(f"  + member {so.member_id} -> cert '{cert.name}' (from item {so.item_id})")
        print(f"  migrated {migrated} award(s)")

        print("\n=== STEP 3: archive Block 100 items ===")
        for item in b100:
            item.archived = True
            print(f"  ~ archived item {item.id} '{item.name}'")

        if commit:
            await db.commit()
            print("\nCOMMITTED.")
        else:
            await db.rollback()
            print("\nDRY-RUN (no changes written). Re-run with --commit to apply.")


if __name__ == "__main__":
    asyncio.run(main("--commit" in sys.argv))
