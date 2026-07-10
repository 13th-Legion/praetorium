"""Derive auto-awarded ribbons/tabs from source-of-truth data.

Automatic awards are NOT stored in member_ribbons; they are computed at render
time from the data that actually defines them (certs, weapons quals, TRADOC
sign-offs, attendance, instructor assignments, rank, flags). This keeps the
rack correct forever with no batch job to run or drift.

Manual/discretionary awards (Dona, commendations, meritorious, recruiter,
esprit, real-world deployment, leadership, mission-leader, instructor-online,
perfect-year, and anything granted via the claim flow) remain in member_ribbons
and are merged with these derived ones by the caller.

Each derived entry: {"code", "device_count", "awarded_at"(best-effort), "source":"auto"}.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.member import Member
from app.models.training import MemberTradoc, MemberCertification, TradocItem
from app.models.weapons_qual import MemberWeaponsQual
from app.models.events import Event, EventRSVP
from app.models.schedule import EventScheduleBlock
from app.models.rank_history import RankHistory

# ── Cert id -> tab code ──────────────────────────────────────────────────────
CERT_TAB = {6: "sabre", 2: "marksman", 1: "sharpshooter", 8: "equites"}
# HAM cert ids in ascending class order -> device tier
HAM_CERTS = [14, 15, 16]  # Technician, General, Extra
# TRADOC item groups for supplemental quals (all required in group => qual)
LANDNAV_ITEMS = {12, 57}          # Basic + Intermediate Land Navigation
COMMS_ITEMS = {10, 53, 54}        # Radio Familiarity + Net Etiquette + Reports
MEDICAL_ITEMS = {4}               # Block 1 Medical
# TRADOC completion = all non-optional items in blocks 1-4 signed off.
TRADOC_BLOCKS = {1, 2, 3, 4}

FTX_DEVICE_THRESHOLDS = [5, 10, 25, 50]  # devices earned at these attendance counts

NCO_GRADES = {"E-5", "E-6", "E-7", "E-8M", "E-8", "E-9"}


async def _was_ever_nco(db: AsyncSession, member: Member) -> bool:
    """NCO ribbon = ACTUALLY HELD an NCO grade (E-5+) at some point.

    Do NOT infer from officer/warrant status: a patched E-4-or-below can win the
    CO election and jump straight to O-3 without ever being an NCO, so
    'is officer -> was NCO' is false. The only valid source is rank history
    (or the current grade being NCO).
    """
    if (member.rank_grade or "") in NCO_GRADES:
        return True
    rows = (await db.execute(
        select(RankHistory.old_rank, RankHistory.new_rank)
        .where(RankHistory.member_id == member.id)
    )).all()
    for old_r, new_r in rows:
        if old_r in NCO_GRADES or new_r in NCO_GRADES:
            return True
    return False


def _ftx_devices(count: int) -> int:
    return sum(1 for t in FTX_DEVICE_THRESHOLDS if count >= t)


async def derive_ribbons(db: AsyncSession, member: Member) -> list[dict]:
    """Return the list of auto-derived ribbon/tab entries for a member."""
    mid = member.id
    out: list[dict] = []

    # ── Certs (tabs + HAM) ──
    cert_ids = set((await db.execute(
        select(MemberCertification.certification_id).where(MemberCertification.member_id == mid)
    )).scalars().all())

    for cid, code in CERT_TAB.items():
        if cid in cert_ids:
            out.append({"code": code, "device_count": 0, "awarded_at": None, "source": "auto"})

    ham_devices = sum(1 for c in HAM_CERTS if c in cert_ids)
    if ham_devices:
        # base ribbon + (ham_devices-1) additional devices (Tech=base, Gen=+1, Extra=+2)
        out.append({"code": "ham", "device_count": ham_devices - 1, "awarded_at": None, "source": "auto"})

    # ── Weapons qual ──
    passed_wq = (await db.execute(
        select(func.count()).select_from(MemberWeaponsQual)
        .where(MemberWeaponsQual.member_id == mid, MemberWeaponsQual.passed.is_(True))
    )).scalar() or 0
    if passed_wq:
        out.append({"code": "qual_weapons", "device_count": 0, "awarded_at": None, "source": "auto"})

    # ── TRADOC-derived: completion + supplemental quals ──
    done_items = set((await db.execute(
        select(MemberTradoc.item_id).where(MemberTradoc.member_id == mid)
    )).scalars().all())

    if LANDNAV_ITEMS.issubset(done_items):
        out.append({"code": "qual_landnav", "device_count": 0, "awarded_at": None, "source": "auto"})
    if COMMS_ITEMS.issubset(done_items):
        out.append({"code": "qual_comms", "device_count": 0, "awarded_at": None, "source": "auto"})
    if MEDICAL_ITEMS.issubset(done_items):
        out.append({"code": "qual_medical", "device_count": 0, "awarded_at": None, "source": "auto"})

    # TRADOC Completion: every non-optional item in blocks 1-4 signed off
    req_items = set((await db.execute(
        select(TradocItem.id).where(
            TradocItem.block.in_(TRADOC_BLOCKS), TradocItem.optional.is_(False)
        )
    )).scalars().all())
    if req_items and req_items.issubset(done_items):
        out.append({"code": "tradoc", "device_count": 0, "awarded_at": None, "source": "auto"})

    # ── Attendance: FTX + MCFTX ──
    ftx_count = (await db.execute(
        select(func.count()).select_from(EventRSVP).join(Event, Event.id == EventRSVP.event_id)
        .where(EventRSVP.member_id == mid, EventRSVP.attended.is_(True), Event.category == "ftx")
    )).scalar() or 0
    if ftx_count:
        out.append({"code": "ftx", "device_count": _ftx_devices(ftx_count), "awarded_at": None, "source": "auto"})

    mcftx_count = (await db.execute(
        select(func.count()).select_from(EventRSVP).join(Event, Event.id == EventRSVP.event_id)
        .where(EventRSVP.member_id == mid, EventRSVP.attended.is_(True), Event.category == "mcftx")
    )).scalar() or 0
    if mcftx_count:
        # base + one device per additional MCFTX beyond the first
        out.append({"code": "mcftx", "device_count": max(0, mcftx_count - 1), "awarded_at": None, "source": "auto"})

    # ── Instructor (FTX classes taught) ──
    taught = (await db.execute(
        select(func.count()).select_from(EventScheduleBlock)
        .where(EventScheduleBlock.instructor_id == mid, EventScheduleBlock.activity_type == "class")
    )).scalar() or 0
    if taught:
        out.append({"code": "instructor_ftx", "device_count": max(0, taught - 1), "awarded_at": None, "source": "auto"})

    # ── Rank / flags ──
    grade = member.rank_grade or ""
    if await _was_ever_nco(db, member):
        out.append({"code": "nco", "device_count": 0, "awarded_at": None, "source": "auto"})
    if grade.startswith("O-") or grade.startswith("W-"):
        out.append({"code": "officer", "device_count": 0, "awarded_at": None, "source": "auto"})
    if getattr(member, "is_founder", False):
        out.append({"code": "founder", "device_count": 0, "awarded_at": None, "source": "auto"})
    if getattr(member, "patch_date", None):
        out.append({"code": "patched", "device_count": 0, "awarded_at": None, "source": "auto"})
    if getattr(member, "join_date", None):
        out.append({"code": "recruit", "device_count": 0, "awarded_at": None, "source": "auto"})

    return out
