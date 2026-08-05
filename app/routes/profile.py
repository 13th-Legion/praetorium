"""Member profile pages — PP-022."""

from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member
from app.models.training import TradocBlock, TradocItem, MemberTradoc, Certification, MemberCertification
from app.models.weapons_qual import MemberWeaponsQual
from app.models.events import Event
from app.models.awards import MemberAward
from app.models.rank_history import RankHistory
from app.models.events import Event, EventRSVP
from app.routes.conduct import get_violations_for_profile

router = APIRouter(prefix="/profile", tags=["profile"])
templates = Jinja2Templates(directory="app/templates")


import re
import httpx
from config import get_settings

async def _fetch_single_nc_login(username: str) -> int:
    settings = get_settings()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{settings.nc_url}/ocs/v2.php/cloud/users/{username}",
                auth=(settings.nc_api_user, settings.nc_api_password),
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                timeout=3
            )
            r.raise_for_status()
            udata = r.json().get("ocs", {}).get("data", {})
            return udata.get("lastLogin", 0)
    except Exception:
        return 0
from markupsafe import Markup

def _format_phone(value: str) -> Markup:
    """Format phone number as (XXX) XXX-XXXX and return clickable tel: link."""
    if not value:
        return "—"
    digits = re.sub(r'\D', '', value)
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]  # strip leading 1
    if len(digits) == 10:
        formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        return Markup(f'<a href="tel:+1{digits}" style="color:#d4a537;text-decoration:none;">{formatted}</a>')
    # Fallback: return original with tel link
    return Markup(f'<a href="tel:{value}" style="color:#d4a537;text-decoration:none;">{value}</a>')

templates.env.filters["phone"] = _format_phone

from app.services import ranks as _ranks


async def _get_ftx_history(member_id: int, db: AsyncSession) -> list:
    """Fetch FTX attendance history for a member, newest first."""
    from sqlalchemy import and_
    result = await db.execute(
        select(Event)
        .join(EventRSVP, EventRSVP.event_id == Event.id)
        .where(
            and_(
                EventRSVP.member_id == member_id,
                EventRSVP.attended == True,
            )
        )
        .order_by(Event.date_start.desc())
    )
    return result.scalars().all()


async def _get_rank_history(member_id: int, db: AsyncSession) -> list:
    """Fetch rank change history for a member, newest first."""
    result = await db.execute(
        select(RankHistory)
        .where(RankHistory.member_id == member_id)
        .order_by(RankHistory.effective_date.desc())
    )
    return result.scalars().all()


async def _get_training_data(member_id: int, db: AsyncSession) -> dict:
    """Fetch TRADOC progress and certifications for a member."""
    # All TRADOC items (archived items are retired duplicates — e.g. the old
    # Block 100 'Certifications' mirror — and must not display or count).
    result = await db.execute(
        select(TradocItem)
        .where(TradocItem.archived.is_(False))
        .order_by(TradocItem.sort_order)
    )
    all_items = result.scalars().all()

    # Block tier map (initial = Basic Training / counts toward patching;
    # advanced = Advanced Training / above-and-beyond, does NOT count).
    bres = await db.execute(select(TradocBlock))
    _all_blocks = bres.scalars().all()
    block_tier = {b.number: (b.tier or "initial") for b in _all_blocks}
    # Display order is the BLOCK's sort_order (not the items' sort_order), so
    # cards render Block 0 (In-Processing) first … Block 5 (Field Standing Tasks)
    # last regardless of how item sort_orders interleave across blocks.
    block_sort = {b.number: (b.sort_order if b.sort_order is not None else b.number) for b in _all_blocks}

    # Member's completed items
    result = await db.execute(
        select(MemberTradoc).where(MemberTradoc.member_id == member_id)
    )
    completed = {mt.item_id: mt for mt in result.scalars().all()}

    # Group by block, carrying each block's tier.
    blocks = {}
    for item in all_items:
        if item.block not in blocks:
            blocks[item.block] = {
                "name": item.block_name,
                "tier": block_tier.get(item.block, "initial"),
                "items": [],
            }
        blocks[item.block]["items"].append({
            "id": item.id,
            "name": item.name,
            "done": item.id in completed,
            "signoff": completed.get(item.id),
            "optional": item.optional,
        })

    # Reorder the block dict by each block's sort_order (Python dicts preserve
    # insertion order, which the template iterates).
    blocks = {
        num: blocks[num]
        for num in sorted(blocks, key=lambda n: (block_sort.get(n, n), n))
    }

    # Patching progress = required (non-optional) items in INITIAL-tier blocks only.
    # Advanced Training blocks (e.g. Block 100) never affect the patched % meter.
    required_items = [
        i for i in all_items
        if not i.optional and block_tier.get(i.block, "initial") == "initial"
    ]
    total = len(required_items)
    done = len([i for i in required_items if i.id in completed])

    # All certifications — alphabetical, but comms certs grouped by precedence
    comms_sort = case(
        (Certification.category == "communications", 1),
        else_=0,
    )
    result = await db.execute(
        select(Certification).order_by(comms_sort, Certification.sort_order, Certification.name)
    )
    all_certs = result.scalars().all()

    # Member's earned certs
    result = await db.execute(
        select(MemberCertification).where(MemberCertification.member_id == member_id)
    )
    earned = {mc.certification_id: mc for mc in result.scalars().all()}

    certs = []
    for cert in all_certs:
        certs.append({
            "id": cert.id,
            "name": cert.name,
            "category": cert.category,
            "icon": cert.icon,
            "earned": cert.id in earned,
            "award": earned.get(cert.id),
        })

    # Awards (Gladii, etc.)
    result = await db.execute(
        select(MemberAward)
        .where(MemberAward.member_id == member_id)
        .order_by(MemberAward.awarded_at.desc())
    )
    awards = result.scalars().all()

    # Weapons Qualification — most recent PASSED qual, with its FTX event.
    wq_row = (await db.execute(
        select(MemberWeaponsQual, Event)
        .join(Event, Event.id == MemberWeaponsQual.event_id)
        .where(MemberWeaponsQual.member_id == member_id, MemberWeaponsQual.passed.is_(True))
        .order_by(Event.date_start.desc())
        .limit(1)
    )).first()
    # Also surface the most recent attempt (pass or fail) for context.
    last_attempt = (await db.execute(
        select(MemberWeaponsQual, Event)
        .join(Event, Event.id == MemberWeaponsQual.event_id)
        .where(MemberWeaponsQual.member_id == member_id)
        .order_by(Event.date_start.desc())
        .limit(1)
    )).first()
    weapons_qual = None
    if wq_row:
        q, ev = wq_row
        weapons_qual = {
            "passed": True,
            "event_title": ev.title,
            "event_id": ev.id,
            "date": (q.qualified_on or (ev.date_start.date() if ev.date_start else None)),
        }
    elif last_attempt:
        q, ev = last_attempt
        weapons_qual = {
            "passed": False,
            "event_title": ev.title,
            "event_id": ev.id,
            "date": (q.qualified_on or (ev.date_start.date() if ev.date_start else None)),
        }

    return {
        "blocks": blocks,
        "total": total,
        "done": done,
        "pct": round(done / total * 100) if total > 0 else 0,
        "certs": certs,
        "awards": awards,
        "weapons_qual": weapons_qual,
    }


async def _get_ribbon_data(member, db: AsyncSession) -> dict:
    """Assemble the member's ribbon display (4 tiers) + promotion-point totals.

    Tiers:
      dona   — decorations, precedence-ordered
      rack   — ribbon rack, precedence-ordered, arranged 4-per-row with the
               partial row on TOP (highest precedence top-center)
      tabs   — qualification tabs
      tenure — Anni Stipendiorum discs computed from join_date (not stored)
    """
    from app.models.ribbons import RibbonCatalog, MemberRibbon
    from app.services.ribbon_points import (
        compute_member_points, tenure_discs, years_of_service,
    )
    from app.services.ribbon_derive import derive_ribbons

    cat_rows = (await db.execute(select(RibbonCatalog).where(RibbonCatalog.active.is_(True)))).scalars().all()
    cat = {c.code: c for c in cat_rows}

    # Manual/claim awards (stored) + auto-derived awards (computed). Manual wins
    # on conflict (e.g. a manually-set device count overrides the derived one).
    mrs = (await db.execute(
        select(MemberRibbon).where(MemberRibbon.member_id == member.id)
    )).scalars().all()
    held = {}  # code -> {device_count, reason, awarded_at}
    for mr in mrs:
        held[mr.ribbon_code] = {
            "device_count": mr.device_count, "reason": mr.reason, "awarded_at": mr.awarded_at,
        }
    for d in await derive_ribbons(db, member):
        if d["code"] not in held:
            held[d["code"]] = {
                "device_count": d["device_count"], "reason": None, "awarded_at": d["awarded_at"],
            }
        else:
            # A stored (manual/claim) row and a derived entry collide. Never let a
            # zeroed/lower stored row wipe out devices the member actually earned:
            # take the higher device count. This self-heals rows created by the
            # ribbon claim flow, which historically inserted device_count=0 and
            # thereby erased derived stars (e.g. instructor_ftx).
            existing = held[d["code"]]
            if d["device_count"] > (existing["device_count"] or 0):
                existing["device_count"] = d["device_count"]

    def _device_meaning(code, dc):
        """Human phrase for what a device count represents on a given ribbon."""
        if code == "ftx":
            th = [5, 10, 25, 50]
            if dc <= 0: return "1–4 FTXs attended"
            reached = th[min(dc, 4) - 1]
            nxt = th[dc] if dc < 4 else None
            return (f"{reached}+ FTXs attended" if not nxt else f"{reached}–{nxt - 1} FTXs attended")
        if code == "mcftx":
            return f"{dc + 1} multi-company FTX{'s' if dc else ''} attended"
        if code == "ham":
            return ["Technician", "General", "Extra"][min(dc, 2)] + " class license"
        if code == "instructor_ftx":
            return f"{dc + 1} FTX class{'es' if dc else ''} taught"
        if code == "recruiter":
            return f"{dc} recruiting device{'s' if dc != 1 else ''}"
        if dc:
            return f"+{dc} device{'s' if dc != 1 else ''}"
        return ""

    dona, rack, tabs = [], [], []
    for code, info in held.items():
        c = cat.get(code)
        if not c:
            continue
        dc = info["device_count"]
        # rich tooltip: name — description (· device meaning) (· citation)
        tip_parts = [c.name]
        if c.description:
            tip_parts.append(c.description)
        dm = _device_meaning(code, dc)
        if dm:
            tip_parts.append(dm)
        if info["reason"]:
            tip_parts.append(f"“{info['reason']}”")
        entry = {
            "code": c.code, "name": c.name, "image": c.image,
            "precedence": c.precedence, "device_count": dc,
            "reason": info["reason"], "awarded_at": info["awarded_at"],
            "description": c.description,
            "device_meaning": dm,
            "tip": "  —  ".join(tip_parts),
        }
        if c.section == "dona":
            dona.append(entry)
        elif c.section == "rack":
            rack.append(entry)
        elif c.section == "tab":
            tabs.append(entry)

    dona.sort(key=lambda e: e["precedence"])
    rack.sort(key=lambda e: e["precedence"])
    tabs.sort(key=lambda e: e["precedence"])

    # Rack rows: 4 per row, partial row on top. With N ribbons, top row holds
    # N % 4 (or 4 if evenly divisible); highest precedence centered on top.
    rack_rows = []
    if rack:
        per = 4
        first = len(rack) % per or per
        idx = 0
        rack_rows.append(rack[idx:idx + first]); idx += first
        while idx < len(rack):
            rack_rows.append(rack[idx:idx + per]); idx += per

    # Tenure discs from join_date
    yos = years_of_service(getattr(member, "join_date", None))
    discs = tenure_discs(yos)
    tenure = []
    for metal in ("gold", "silver", "bronze"):
        for _ in range(discs[metal]):
            tenure.append({"metal": metal, "image": f"discs/anni_{metal}.png"})

    points = await compute_member_points(db, member.id, getattr(member, "join_date", None))

    return {
        "dona": dona,
        "rack": rack,
        "rack_rows": rack_rows,
        "tabs": tabs,
        "tenure": tenure,
        "tenure_years": yos,
        "has_any": bool(dona or rack or tabs or tenure),
        "points": {
            "lifetime": points.lifetime,
            "since_last_promotion": points.since_last_promotion,
            "lifetime_tenure": points.lifetime_tenure,
            "lifetime_ribbons": points.lifetime_ribbons,
            "last_promotion_date": points.last_promotion_date,
        },
    }


@router.get("")
@require_auth
async def my_profile(request: Request, db: AsyncSession = Depends(get_db)):
    """Show the logged-in user's own profile."""
    user = get_current_user(request)
    username = user["username"]

    result = await db.execute(
        select(Member).where(Member.nc_username == username)
    )
    member = result.scalar_one_or_none()

    if not member:
        return templates.TemplateResponse("pages/profile.html", {
            "request": request,
            "user": user,
            "member": None,
            "rank_abbr": _ranks.abbr_map(),
            "rank_title": _ranks.title_map(),
            "is_own": True,
            "can_see_pii": True,
            "training": None,
            "rank_history": [],
            "now": datetime.utcnow(),
        })

    training = await _get_training_data(member.id, db)
    rank_history = await _get_rank_history(member.id, db)
    ftx_history = await _get_ftx_history(member.id, db)
    conduct_violations = await get_violations_for_profile(member.id, db)

    # Build My Documents list (view links + signed status for signable docs)
    my_documents = []
    for _k, _t, _signable, _attr in MY_DOCUMENTS:
        _signed = getattr(member, _attr, None) if (_signable and _attr) else None
        my_documents.append({"key": _k, "title": _t, "signable": _signable, "signed_at": _signed})

    # Fetch NC last login for this member
    nc_last_login = None
    if member.nc_username:
        try:
            login_ms = await _fetch_single_nc_login(member.nc_username)
            if login_ms and login_ms > 0:
                from zoneinfo import ZoneInfo; nc_last_login = datetime.fromtimestamp(login_ms / 1000, tz=ZoneInfo("America/Chicago"))
        except Exception:
            pass

    ribbons = await _get_ribbon_data(member, db)

    return templates.TemplateResponse("pages/profile.html", {
        "request": request,
        "user": user,
        "member": member,
        "rank_abbr": _ranks.abbr_map(),
        "rank_title": _ranks.title_map(),
        "is_own": True,
        "can_see_pii": True,
        "training": training,
        "rank_history": rank_history,
        "ftx_history": ftx_history,
        "conduct_violations": conduct_violations,
        "nc_last_login": nc_last_login,
        "my_documents": my_documents,
        "ribbons": ribbons,
        "now": datetime.utcnow(),
    })


@router.get("/{member_id}")
@require_auth
async def view_profile(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """View another member's profile."""
    user = get_current_user(request)
    user_roles = set(user.get("roles", []))

    result = await db.execute(
        select(Member).where(Member.id == member_id)
    )
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # PII visible to leadership, S1, or self
    is_own = (member.nc_username == user["username"])
    can_see_pii = is_own or bool(user_roles & {"admin", "command", "leader", "officer", "nco", "s1"})

    training = await _get_training_data(member.id, db)
    rank_history = await _get_rank_history(member.id, db)
    ftx_history = await _get_ftx_history(member.id, db)
    conduct_violations = await get_violations_for_profile(member.id, db)

    # Build My Documents list (view links + signed status for signable docs)
    my_documents = []
    for _k, _t, _signable, _attr in MY_DOCUMENTS:
        _signed = getattr(member, _attr, None) if (_signable and _attr) else None
        my_documents.append({"key": _k, "title": _t, "signable": _signable, "signed_at": _signed})

    # Fetch NC last login for this member
    nc_last_login = None
    if member.nc_username:
        try:
            login_ms = await _fetch_single_nc_login(member.nc_username)
            if login_ms and login_ms > 0:
                from zoneinfo import ZoneInfo; nc_last_login = datetime.fromtimestamp(login_ms / 1000, tz=ZoneInfo("America/Chicago"))
        except Exception:
            pass

    ribbons = await _get_ribbon_data(member, db)

    return templates.TemplateResponse("pages/profile.html", {
        "request": request,
        "user": user,
        "member": member,
        "rank_abbr": _ranks.abbr_map(),
        "rank_title": _ranks.title_map(),
        "is_own": is_own,
        "can_see_pii": can_see_pii,
        "training": training,
        "rank_history": rank_history,
        "ftx_history": ftx_history,
        "conduct_violations": conduct_violations,
        "nc_last_login": nc_last_login,
        "my_documents": my_documents,
        "ribbons": ribbons,
        "now": datetime.utcnow(),
    })

# ─── My Documents (read-only viewer for unit documents) ──────────────────────

# Document registry: key -> (title, signable flag, signed_at member attribute)
MY_DOCUMENTS = [
    ("activity_policy", "Activity Policy", True, "activity_policy_signed_at"),
    ("code_of_conduct", "Code of Conduct", True, "code_of_conduct_signed_at"),
    ("bylaws", "TSM By-Laws", True, "bylaws_signed_at"),
    ("nda", "Non-Disclosure Agreement", True, "nda_signed_at"),
    ("general_waiver", "General Waiver & Release of Liability", True, "waiver_signed_at"),
]


def _get_doc_content(doc_type: str):
    """Return (title, html_content) for a document key, or (None, None)."""
    from app.routes.doc_texts import (
        CODE_OF_CONDUCT_TEXT, BYLAWS_TEXT, ACTIVITY_POLICY_TEXT,
    )
    from app.routes.s1_admin import NDA_TEXT, WAIVER_TEXT
    mapping = {
        "activity_policy": ("Activity Policy", ACTIVITY_POLICY_TEXT),
        "code_of_conduct": ("Code of Conduct", CODE_OF_CONDUCT_TEXT),
        "bylaws": ("TSM By-Laws", BYLAWS_TEXT),
        "nda": ("Non-Disclosure Agreement", NDA_TEXT),
        "general_waiver": ("General Waiver & Release of Liability", WAIVER_TEXT),
    }
    return mapping.get(doc_type, (None, None))


@router.get("/document/{doc_type}")
@require_auth
async def view_document(request: Request, doc_type: str, db: AsyncSession = Depends(get_db)):
    """Read-only document viewer for any logged-in member."""
    user = get_current_user(request)
    title, content = _get_doc_content(doc_type)
    if not content:
        raise HTTPException(status_code=404, detail="Document not found")
    return templates.TemplateResponse("pages/view_document.html", {
        "request": request,
        "user": user,
        "doc_title": title,
        "doc_content": content,
        "doc_type": doc_type,
    })
