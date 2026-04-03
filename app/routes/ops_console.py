"""Event Operations Console routes — PP-070.

Provides the live ops console for any event:
  - QR check-in display + HMAC token validation
  - Live roster (HTMX polling)
  - Battle buddy pairing
  - Guard duty slots + assignment
  - Vexillation (mission team) management
  - Walk-in guest management
  - Manual check-in by S1
"""

import hashlib
import hmac
import io
import math
import time
from datetime import datetime
from typing import Optional

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_, delete
from sqlalchemy.orm import selectinload

from app.auth import require_auth, require_role, get_current_user
from app.database import async_session
from app.models.events import (
    Event, EventRSVP, EventGuest, EventBuddyPair,
    EventGuardSlot, EventGuardDuty, EventVexillation, EventVexillationAssignment,
)
from app.models.member import Member
from config import get_settings

router = APIRouter(tags=["ops-console"])
templates = Jinja2Templates(directory="app/templates")

# ─── CDT Filter (match events.py convention) ─────────────────────────────────

from zoneinfo import ZoneInfo
_CDT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


def _to_cdt(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_CDT)


templates.env.filters["cdt"] = _to_cdt

# ─── Constants ────────────────────────────────────────────────────────────────

TACTICAL_CATEGORIES = {"ftx", "mcftx", "training_course"}
OPS_ROLES = ("s1", "command", "admin")
S1_CMD_ROLES = ("s1", "command", "admin")
S1_S2_CMD_ROLES = ("s1", "s2", "command", "admin")
S3_CMD_ROLES = ("s3", "command", "admin")
S1_S3_CMD_ROLES = ("s1", "s3", "command", "admin")

TOKEN_WINDOW_SECONDS = 900  # 15 min rotation


# ─── QR Token Helpers ────────────────────────────────────────────────────────

def _qr_rotation_window(ts: Optional[float] = None) -> int:
    """Return current 15-min rotation window index."""
    return math.floor((ts or time.time()) / TOKEN_WINDOW_SECONDS)


def _generate_qr_token(event_id: int, window: int, secret: str) -> str:
    """HMAC-SHA256(event_id + window, secret)."""
    msg = f"{event_id}:{window}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()  # type: ignore[attr-defined]


def _validate_qr_token(event_id: int, token: str, secret: str) -> bool:
    """Validate token for current window ± 1 (grace period)."""
    current = _qr_rotation_window()
    for window in (current - 1, current, current + 1):
        expected = _generate_qr_token(event_id, window, secret)
        if hmac.compare_digest(expected, token):
            return True
    return False


# ─── Auth role helper ────────────────────────────────────────────────────────

def _user_has_role(user: dict, *roles: str) -> bool:
    user_roles = set(user.get("roles", []))
    return bool(user_roles.intersection(set(roles)))


# ─── Ops Console — Main Page ─────────────────────────────────────────────────

@router.get("/events/{event_id}/ops", response_class=HTMLResponse)
@require_role(*OPS_ROLES)
async def ops_console(request: Request, event_id: int):
    """Main ops console page for an event."""
    user = get_current_user(request)
    settings = get_settings()

    async with async_session() as db:
        event = await _get_event_or_404(db, event_id)
        roster_rows = await _build_roster(db, event)
        guard_slots = await _get_guard_slots(db, event_id)
        vexillations = await _get_vexillations(db, event_id)
        checked_in_members = await _get_checked_in_members(db, event_id)
        member_map = await _get_member_map(db, event_id)

    show_tactical = event.category in TACTICAL_CATEGORIES
    display_mode = request.query_params.get("display", "normal")  # "tv" or "normal"

    # Generate QR code SVG
    window = _qr_rotation_window()
    token = _generate_qr_token(event_id, window, settings.secret_key)
    qr_url = str(request.base_url).rstrip("/") + f"/events/{event_id}/checkin?token={token}"
    qr_svg = _make_qr_svg(qr_url)

    # Next refresh time
    next_refresh_unix = (window + 1) * TOKEN_WINDOW_SECONDS
    from datetime import timezone
    next_refresh = datetime.fromtimestamp(next_refresh_unix, tz=_CDT)

    return templates.TemplateResponse("pages/ops_console.html", {
        "request": request,
        "user": user,
        "event": event,
        "roster": roster_rows,
        "guard_slots": guard_slots,
        "vexillations": vexillations,
        "checked_in_members": checked_in_members,
        "qr_svg": qr_svg,
        "qr_url": qr_url,
        "next_refresh": next_refresh,
        "show_tactical": show_tactical,
        "display_mode": display_mode,
        "can_checkin": _user_has_role(user, *OPS_ROLES),
        "can_add_guest": _user_has_role(user, *S1_CMD_ROLES),
        "can_buddy": _user_has_role(user, *S1_CMD_ROLES),
        "can_guard": _user_has_role(user, *S1_S2_CMD_ROLES),
        "can_vex_create": _user_has_role(user, *S3_CMD_ROLES),
        "can_vex_assign": _user_has_role(user, *S1_S3_CMD_ROLES),
        "member_map": member_map,
    })


# ─── QR Code endpoint ─────────────────────────────────────────────────────────

@router.get("/events/{event_id}/ops/qr", response_class=Response)
@require_role(*OPS_ROLES)
async def ops_qr_code(request: Request, event_id: int):
    """Return a QR code PNG image for the current check-in token."""
    user = get_current_user(request)
    settings = get_settings()

    async with async_session() as db:
        await _get_event_or_404(db, event_id)

    window = _qr_rotation_window()
    token = _generate_qr_token(event_id, window, settings.secret_key)
    qr_url = str(request.base_url).rstrip("/") + f"/events/{event_id}/checkin?token={token}"

    img = qrcode.make(qr_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")


# ─── QR Check-in (member scans QR code) ──────────────────────────────────────

@router.post("/events/{event_id}/checkin", response_class=HTMLResponse)
@require_auth
async def qr_checkin(request: Request, event_id: int, token: str = ""):
    """Process QR code check-in. Token passed as query param."""
    user = get_current_user(request)
    settings = get_settings()

    # Also support token in query params (GET redirect from QR scan)
    if not token:
        token = request.query_params.get("token", "")

    if not _validate_qr_token(event_id, token, settings.secret_key):
        return templates.TemplateResponse("pages/checkin_result.html", {
            "request": request,
            "user": user,
            "success": False,
            "message": "Invalid or expired check-in token. Ask S1 for a fresh QR code.",
        })

    async with async_session() as db:
        event = await _get_event_or_404(db, event_id)

        # Look up member
        username = user.get("username")
        result = await db.execute(select(Member).where(Member.nc_username == username))
        member = result.scalar_one_or_none()
        if not member:
            return templates.TemplateResponse("pages/checkin_result.html", {
                "request": request,
                "user": user,
                "success": False,
                "message": "Member record not found. Contact S1.",
            })

        # Find or create RSVP
        result = await db.execute(
            select(EventRSVP).where(
                and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member.id)
            )
        )
        rsvp = result.scalar_one_or_none()

        if rsvp and rsvp.checked_in:
            return templates.TemplateResponse("pages/checkin_result.html", {
                "request": request,
                "user": user,
                "success": True,
                "message": f"Already checked in, {member.first_name}. Welcome back.",
                "event": event,
            })

        is_walkin = False
        if rsvp:
            if rsvp.status not in ("attending",):
                is_walkin = True
            rsvp.checked_in = True
            rsvp.checked_in_at = datetime.utcnow()
            rsvp.checked_in_by = "qr_self"
        else:
            # Create a new RSVP record flagged as walk-in
            is_walkin = True
            rsvp = EventRSVP(
                event_id=event_id,
                member_id=member.id,
                status="attending",
                checked_in=True,
                checked_in_at=datetime.utcnow(),
                checked_in_by="qr_walkin",
            )
            db.add(rsvp)

        await db.commit()

    return templates.TemplateResponse("pages/checkin_result.html", {
        "request": request,
        "user": user,
        "success": True,
        "message": f"Checked in: {member.first_name} {member.last_name}{'  (walk-in flagged)' if is_walkin else ''}",
        "event": event,
        "is_walkin": is_walkin,
    })


@router.get("/events/{event_id}/checkin", response_class=HTMLResponse)
@require_auth
async def qr_checkin_get(request: Request, event_id: int, token: str = ""):
    """GET check-in from QR scan — validate then process."""
    # Delegate to POST handler logic directly
    user = get_current_user(request)
    settings = get_settings()

    if not _validate_qr_token(event_id, token, settings.secret_key):
        return templates.TemplateResponse("pages/checkin_result.html", {
            "request": request,
            "user": user,
            "success": False,
            "message": "Invalid or expired check-in token. Ask S1 for a fresh QR code.",
        })

    async with async_session() as db:
        event = await _get_event_or_404(db, event_id)

        username = user.get("username")
        result = await db.execute(select(Member).where(Member.nc_username == username))
        member = result.scalar_one_or_none()
        if not member:
            return templates.TemplateResponse("pages/checkin_result.html", {
                "request": request,
                "user": user,
                "success": False,
                "message": "Member record not found. Contact S1.",
            })

        result = await db.execute(
            select(EventRSVP).where(
                and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member.id)
            )
        )
        rsvp = result.scalar_one_or_none()

        if rsvp and rsvp.checked_in:
            return templates.TemplateResponse("pages/checkin_result.html", {
                "request": request,
                "user": user,
                "success": True,
                "message": f"Already checked in, {member.first_name}.",
                "event": event,
            })

        is_walkin = False
        if rsvp:
            is_walkin = rsvp.status not in ("attending",)
            rsvp.checked_in = True
            rsvp.checked_in_at = datetime.utcnow()
            rsvp.checked_in_by = "qr_self"
        else:
            is_walkin = True
            rsvp = EventRSVP(
                event_id=event_id,
                member_id=member.id,
                status="attending",
                checked_in=True,
                checked_in_at=datetime.utcnow(),
                checked_in_by="qr_walkin",
            )
            db.add(rsvp)

        await db.commit()

    return templates.TemplateResponse("pages/checkin_result.html", {
        "request": request,
        "user": user,
        "success": True,
        "message": f"Checked in: {member.first_name} {member.last_name}{'  (walk-in flagged)' if is_walkin else ''}",
        "event": event,
        "is_walkin": is_walkin,
    })


# ─── Live Roster Partial ──────────────────────────────────────────────────────

@router.get("/events/{event_id}/ops/roster", response_class=HTMLResponse)
@require_role(*OPS_ROLES)
async def ops_roster(request: Request, event_id: int):
    """HTMX partial: live roster table body (polls every 10s)."""
    user = get_current_user(request)

    async with async_session() as db:
        event = await _get_event_or_404(db, event_id)
        roster_rows = await _build_roster(db, event)
        guard_slots = await _get_guard_slots(db, event_id)
        vexillations = await _get_vexillations(db, event_id)

    show_tactical = event.category in TACTICAL_CATEGORIES

    return templates.TemplateResponse("partials/ops_roster.html", {
        "request": request,
        "user": user,
        "event": event,
        "roster": roster_rows,
        "guard_slots": guard_slots,
        "vexillations": vexillations,
        "show_tactical": show_tactical,
        "display_mode": request.query_params.get("display", "normal"),
        "can_checkin": _user_has_role(user, *OPS_ROLES),
        "can_buddy": _user_has_role(user, *S1_CMD_ROLES),
        "can_guard": _user_has_role(user, *S1_S2_CMD_ROLES),
        "can_vex_assign": _user_has_role(user, *S1_S3_CMD_ROLES),
    })


# ─── Manual Check-in ─────────────────────────────────────────────────────────

@router.post("/events/{event_id}/ops/override-rsvp", response_class=HTMLResponse)
@require_role(*OPS_ROLES)
async def override_rsvp(
    request: Request,
    event_id: int,
    member_id: int = Form(...),
    status: str = Form(...),
):
    """Command/Leader manually overrides a member's RSVP status."""
    if status not in ("attending", "declined", "pending"):
        return HTMLResponse("Invalid status", status_code=400)

    async with async_session() as db:
        event = await _get_event_or_404(db, event_id)

        rsvp_result = await db.execute(
            select(EventRSVP).where(
                and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member_id)
            )
        )
        rsvp = rsvp_result.scalar_one_or_none()

        if rsvp:
            rsvp.status = status
            rsvp.updated_at = datetime.utcnow()
        else:
            rsvp = EventRSVP(
                event_id=event_id,
                member_id=member_id,
                status=status,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(rsvp)

        await db.commit()

        # Render updated roster
        user = request.session.get("user", {})
        roster_rows = await _build_roster(db, event)
        guard_slots = await _get_guard_slots(db, event_id)
        vexillations = await _get_vexillations(db, event_id)
        
        return templates.TemplateResponse("partials/ops_roster.html", {
            "request": request,
            "user": user,
            "event": event,
            "roster": roster_rows,
            "show_tactical": event.category in TACTICAL_CATEGORIES,
            "can_checkin": _user_has_role(user, *OPS_ROLES),
            "guard_slots": guard_slots,
            "vexillations": vexillations,
        })


@router.post("/events/{event_id}/ops/manual-checkin", response_class=HTMLResponse)
@require_role(*OPS_ROLES)
async def manual_checkin(
    request: Request,
    event_id: int,
    member_id: int = Form(...),
):
    """S1 manually checks in a member."""
    user = get_current_user(request)
    username = user.get("username", "unknown")

    async with async_session() as db:
        await _get_event_or_404(db, event_id)

        result = await db.execute(
            select(EventRSVP).where(
                and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member_id)
            )
        )
        rsvp = result.scalar_one_or_none()

        if rsvp:
            rsvp.checked_in = True
            rsvp.checked_in_at = datetime.utcnow()
            rsvp.checked_in_by = username
        else:
            rsvp = EventRSVP(
                event_id=event_id,
                member_id=member_id,
                status="attending",
                checked_in=True,
                checked_in_at=datetime.utcnow(),
                checked_in_by=username,
            )
            db.add(rsvp)

        await db.commit()

    # Return updated roster partial
    return RedirectResponse(url=f"/events/{event_id}/ops/roster", status_code=303)


# ─── Battle Buddy ─────────────────────────────────────────────────────────────

@router.post("/events/{event_id}/ops/buddy", response_class=HTMLResponse)
@require_role(*S1_CMD_ROLES)
async def pair_buddy(
    request: Request,
    event_id: int,
    member_a_id: Optional[int] = Form(None),
    member_b_id: Optional[int] = Form(None),
    guest_a_id: Optional[int] = Form(None),
    guest_b_id: Optional[int] = Form(None),
):
    """Create a battle buddy pairing."""
    user = get_current_user(request)

    if not member_a_id and not guest_a_id:
        raise HTTPException(status_code=400, detail="At least one person (A) required")

    async with async_session() as db:
        await _get_event_or_404(db, event_id)

        pair = EventBuddyPair(
            event_id=event_id,
            member_a_id=member_a_id,
            member_b_id=member_b_id,
            guest_a_id=guest_a_id,
            guest_b_id=guest_b_id,
            created_at=datetime.utcnow(),
        )
        db.add(pair)
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops/roster", status_code=303)


@router.delete("/events/{event_id}/ops/buddy/{pair_id}", response_class=HTMLResponse)
@require_role(*S1_CMD_ROLES)
async def unpair_buddy(request: Request, event_id: int, pair_id: int):
    """Remove a battle buddy pairing (HTMX DELETE)."""
    async with async_session() as db:
        result = await db.execute(
            select(EventBuddyPair).where(
                and_(EventBuddyPair.id == pair_id, EventBuddyPair.event_id == event_id)
            )
        )
        pair = result.scalar_one_or_none()
        if not pair:
            raise HTTPException(status_code=404, detail="Buddy pair not found")
        await db.delete(pair)
        await db.commit()

    return HTMLResponse(content="", status_code=200)


@router.post("/events/{event_id}/ops/buddy/{pair_id}/delete", response_class=HTMLResponse)
@require_role(*S1_CMD_ROLES)
async def unpair_buddy_post(request: Request, event_id: int, pair_id: int):
    """Remove a battle buddy pairing (POST fallback for non-JS)."""
    async with async_session() as db:
        result = await db.execute(
            select(EventBuddyPair).where(
                and_(EventBuddyPair.id == pair_id, EventBuddyPair.event_id == event_id)
            )
        )
        pair = result.scalar_one_or_none()
        if not pair:
            raise HTTPException(status_code=404, detail="Buddy pair not found")
        await db.delete(pair)
        await db.commit()

    # HTMX request → return roster partial; otherwise redirect
    if request.headers.get("HX-Request"):
        return RedirectResponse(url=f"/events/{event_id}/ops/roster", status_code=303)
    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


# ─── Guard Duty ───────────────────────────────────────────────────────────────

@router.post("/events/{event_id}/ops/guard/slots", response_class=HTMLResponse)
@require_role(*S1_S2_CMD_ROLES)
async def configure_guard_slots(
    request: Request,
    event_id: int,
    slot_count: int = Form(...),
    slot_labels: str = Form(""),  # comma-separated labels
):
    """Configure guard duty slots for an event."""
    async with async_session() as db:
        await _get_event_or_404(db, event_id)

        # Remove existing slots
        await db.execute(
            delete(EventGuardSlot).where(EventGuardSlot.event_id == event_id)
        )

        labels = [l.strip() for l in slot_labels.split(",") if l.strip()]
        for i in range(1, slot_count + 1):
            label = labels[i - 1] if i <= len(labels) else None
            slot = EventGuardSlot(
                event_id=event_id,
                slot_number=i,
                slot_label=label,
                created_at=datetime.utcnow(),
            )
            db.add(slot)

        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


@router.post("/events/{event_id}/ops/guard/assign", response_class=HTMLResponse)
@require_role(*S1_S2_CMD_ROLES)
async def assign_guard(
    request: Request,
    event_id: int,
    slot_number: int = Form(...),
    member_id: Optional[int] = Form(None),
    guest_id: Optional[int] = Form(None),
):
    """Assign a member to a guard duty slot."""
    user = get_current_user(request)
    username = user.get("username", "unknown")

    async with async_session() as db:
        await _get_event_or_404(db, event_id)

        # Get slot info
        slot_result = await db.execute(
            select(EventGuardSlot).where(
                and_(EventGuardSlot.event_id == event_id, EventGuardSlot.slot_number == slot_number)
            )
        )
        slot = slot_result.scalar_one_or_none()

        duty = EventGuardDuty(
            event_id=event_id,
            slot_id=slot.id if slot else None,
            slot_number=slot_number,
            slot_label=slot.slot_label if slot else None,
            member_id=member_id,
            guest_id=guest_id,
            assigned_by=username,
            created_at=datetime.utcnow(),
        )
        db.add(duty)
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops/roster", status_code=303)


@router.delete("/events/{event_id}/ops/guard/{assignment_id}", response_class=HTMLResponse)
@require_role(*S1_S2_CMD_ROLES)
async def unassign_guard(request: Request, event_id: int, assignment_id: int):
    """Remove a guard duty assignment (HTMX DELETE)."""
    async with async_session() as db:
        result = await db.execute(
            select(EventGuardDuty).where(
                and_(EventGuardDuty.id == assignment_id, EventGuardDuty.event_id == event_id)
            )
        )
        duty = result.scalar_one_or_none()
        if not duty:
            raise HTTPException(status_code=404, detail="Assignment not found")
        await db.delete(duty)
        await db.commit()

    return HTMLResponse(content="", status_code=200)


@router.post("/events/{event_id}/ops/guard/{assignment_id}/delete", response_class=HTMLResponse)
@require_role(*S1_S2_CMD_ROLES)
async def unassign_guard_post(request: Request, event_id: int, assignment_id: int):
    """Remove a guard duty assignment (POST fallback)."""
    async with async_session() as db:
        result = await db.execute(
            select(EventGuardDuty).where(
                and_(EventGuardDuty.id == assignment_id, EventGuardDuty.event_id == event_id)
            )
        )
        duty = result.scalar_one_or_none()
        if not duty:
            raise HTTPException(status_code=404, detail="Assignment not found")
        await db.delete(duty)
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


@router.post("/events/{event_id}/ops/guard/auto-assign", response_class=HTMLResponse)
@require_role(*S1_S2_CMD_ROLES)
async def auto_assign_guard(request: Request, event_id: int):
    """Auto-distribute checked-in members evenly across guard slots."""
    user = get_current_user(request)
    username = user.get("username", "unknown")

    async with async_session() as db:
        await _get_event_or_404(db, event_id)

        # Get slots
        slot_result = await db.execute(
            select(EventGuardSlot).where(EventGuardSlot.event_id == event_id)
            .order_by(EventGuardSlot.slot_number)
        )
        slots = slot_result.scalars().all()
        if not slots:
            raise HTTPException(status_code=400, detail="No guard slots configured")

        # Get checked-in members not already assigned
        assigned_result = await db.execute(
            select(EventGuardDuty.member_id).where(
                and_(EventGuardDuty.event_id == event_id, EventGuardDuty.member_id.isnot(None))
            )
        )
        already_assigned = {row[0] for row in assigned_result.all()}

        checkin_result = await db.execute(
            select(EventRSVP.member_id).where(
                and_(EventRSVP.event_id == event_id, EventRSVP.checked_in == True)
            )
        )
        checked_in_ids = [row[0] for row in checkin_result.all() if row[0] not in already_assigned]

        # Distribute
        for i, member_id in enumerate(checked_in_ids):
            slot = slots[i % len(slots)]
            duty = EventGuardDuty(
                event_id=event_id,
                slot_id=slot.id,
                slot_number=slot.slot_number,
                slot_label=slot.slot_label,
                member_id=member_id,
                assigned_by=username,
                created_at=datetime.utcnow(),
            )
            db.add(duty)

        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


# ─── Vexillations ─────────────────────────────────────────────────────────────

@router.post("/events/{event_id}/ops/vexillation", response_class=HTMLResponse)
@require_role(*S3_CMD_ROLES)
async def create_vexillation(
    request: Request,
    event_id: int,
    name: str = Form(...),
):
    """Create a vexillation (mission team) for an event."""
    user = get_current_user(request)
    username = user.get("username", "unknown")

    async with async_session() as db:
        await _get_event_or_404(db, event_id)

        vex = EventVexillation(
            event_id=event_id,
            name=name.strip(),
            field_status="in_assembly",
            created_by=username,
            created_at=datetime.utcnow(),
        )
        db.add(vex)
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


@router.delete("/events/{event_id}/ops/vexillation/{vex_id}", response_class=HTMLResponse)
@require_role(*S3_CMD_ROLES)
async def delete_vexillation(request: Request, event_id: int, vex_id: int):
    """Delete a vexillation and all its assignments (HTMX DELETE)."""
    async with async_session() as db:
        result = await db.execute(
            select(EventVexillation).where(
                and_(EventVexillation.id == vex_id, EventVexillation.event_id == event_id)
            )
        )
        vex = result.scalar_one_or_none()
        if not vex:
            raise HTTPException(status_code=404, detail="Vexillation not found")
        await db.delete(vex)
        await db.commit()

    return HTMLResponse(content="", status_code=200)


@router.post("/events/{event_id}/ops/vexillation/{vex_id}/delete", response_class=HTMLResponse)
@require_role(*S3_CMD_ROLES)
async def delete_vexillation_post(request: Request, event_id: int, vex_id: int):
    """Delete a vexillation (POST fallback)."""
    user = get_current_user(request)

    async with async_session() as db:
        result = await db.execute(
            select(EventVexillation).where(
                and_(EventVexillation.id == vex_id, EventVexillation.event_id == event_id)
            )
        )
        vex = result.scalar_one_or_none()
        if not vex:
            raise HTTPException(status_code=404, detail="Vexillation not found")
        await db.delete(vex)
        await db.commit()

    # HTMX → return updated vex summary partial
    if request.headers.get("HX-Request"):
        async with async_session() as db:
            event = await _get_event_or_404(db, event_id)
            vexillations = await _get_vexillations(db, event_id)
            member_map = await _get_member_map(db, event_id)
        return templates.TemplateResponse("partials/ops_vex_summary.html", {
            "request": request,
            "user": user,
            "event": event,
            "vexillations": vexillations,
            "member_map": member_map,
            "can_vex_create": _user_has_role(user, *S3_CMD_ROLES),
        })
    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


@router.post("/events/{event_id}/ops/vexillation/{vex_id}/assign", response_class=HTMLResponse)
@require_role(*S1_S3_CMD_ROLES)
async def assign_vexillation(
    request: Request,
    event_id: int,
    vex_id: int,
    member_id: Optional[int] = Form(None),
    guest_id: Optional[int] = Form(None),
):
    """Assign a member or guest to a vexillation."""
    user = get_current_user(request)
    username = user.get("username", "unknown")

    async with async_session() as db:
        await _get_event_or_404(db, event_id)

        # Remove any existing vexillation assignment for this member at this event
        if member_id:
            await db.execute(
                delete(EventVexillationAssignment).where(
                    and_(
                        EventVexillationAssignment.event_id == event_id,
                        EventVexillationAssignment.member_id == member_id,
                    )
                )
            )

        assignment = EventVexillationAssignment(
            event_id=event_id,
            vexillation_id=vex_id,
            member_id=member_id,
            guest_id=guest_id,
            assigned_by=username,
            created_at=datetime.utcnow(),
        )
        db.add(assignment)
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops/roster", status_code=303)


@router.post("/events/{event_id}/ops/vexillation/{vex_id}/status", response_class=HTMLResponse)
@require_role(*S3_CMD_ROLES)
async def set_vexillation_status(
    request: Request,
    event_id: int,
    vex_id: int,
    field_status: str = Form(...),
):
    """Set the field status of a vexillation: in_assembly | in_field | released."""
    valid_statuses = {"in_assembly", "in_field", "released"}
    if field_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status: {field_status}")

    async with async_session() as db:
        result = await db.execute(
            select(EventVexillation).where(
                and_(EventVexillation.id == vex_id, EventVexillation.event_id == event_id)
            )
        )
        vex = result.scalar_one_or_none()
        if not vex:
            raise HTTPException(status_code=404, detail="Vexillation not found")
        vex.field_status = field_status
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


@router.post("/events/{event_id}/ops/vexillation/{vex_id}/commander", response_class=HTMLResponse)
@require_role(*S3_CMD_ROLES)
async def set_vexillation_commander(
    request: Request,
    event_id: int,
    vex_id: int,
    commander_id: Optional[int] = Form(None),
):
    """Set or clear the commander (Praepositus) of a vexillation."""
    async with async_session() as db:
        result = await db.execute(
            select(EventVexillation).where(
                and_(EventVexillation.id == vex_id, EventVexillation.event_id == event_id)
            )
        )
        vex = result.scalar_one_or_none()
        if not vex:
            raise HTTPException(status_code=404, detail="Vexillation not found")
        vex.commander_id = commander_id
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


# ─── Guest Management ─────────────────────────────────────────────────────────

@router.post("/events/{event_id}/ops/guest", response_class=HTMLResponse)
@require_role(*S1_CMD_ROLES)
async def add_walkin_guest(
    request: Request,
    event_id: int,
    first_name: str = Form(...),
    last_name: str = Form(...),
    relation: str = Form("other"),
    sponsor_id: int = Form(...),
    notes: str = Form(""),
    phone: str = Form(""),
    waiver_ack: bool = Form(False),
):
    """Add a walk-in guest from the ops console."""
    user = get_current_user(request)
    username = user.get("username", "unknown")

    async with async_session() as db:
        await _get_event_or_404(db, event_id)

        guest = EventGuest(
            event_id=event_id,
            sponsor_id=sponsor_id,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            relation=relation,
            notes=notes.strip() or None,
            phone=phone.strip() or None,
            waiver_ack=waiver_ack,
            is_walkin=True,
            checked_in_at=datetime.utcnow(),
            registered_by=username,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(guest)
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops/roster", status_code=303)


# ─── Guard Config Partial ─────────────────────────────────────────────────────

@router.get("/events/{event_id}/ops/guard/config", response_class=HTMLResponse)
@require_role(*S1_S2_CMD_ROLES)
async def ops_guard_config(request: Request, event_id: int):
    """HTMX partial: guard slot configuration panel."""
    user = get_current_user(request)

    async with async_session() as db:
        event = await _get_event_or_404(db, event_id)
        guard_slots = await _get_guard_slots(db, event_id)
        guard_duties = await _get_guard_duties(db, event_id)
        member_map = await _get_member_map(db, event_id)

    return templates.TemplateResponse("partials/ops_guard_config.html", {
        "request": request,
        "user": user,
        "event": event,
        "guard_slots": guard_slots,
        "guard_duties": guard_duties,
        "member_map": member_map,
    })


# ─── Vexillation Summary Partial ──────────────────────────────────────────────

@router.get("/events/{event_id}/ops/vexillations", response_class=HTMLResponse)
@require_role(*OPS_ROLES)
async def ops_vex_summary(request: Request, event_id: int):
    """HTMX partial: vexillation summary with field status toggles."""
    user = get_current_user(request)

    async with async_session() as db:
        event = await _get_event_or_404(db, event_id)
        vexillations = await _get_vexillations(db, event_id)
        member_map = await _get_member_map(db, event_id)

    return templates.TemplateResponse("partials/ops_vex_summary.html", {
        "request": request,
        "user": user,
        "event": event,
        "vexillations": vexillations,
        "member_map": member_map,
        "can_vex_create": _user_has_role(user, *S3_CMD_ROLES),
    })


# ─── DB Query Helpers ─────────────────────────────────────────────────────────

async def _get_event_or_404(db, event_id: int) -> Event:
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


async def _build_roster(db, event: Event) -> list[dict]:
    """Build roster rows combining RSVPs, check-in status, buddy pairs, guard duty, vexillations."""
    event_id = event.id

    # Get all RSVPs (attending or checked-in) — exclude separated/inactive/blacklisted members
    rsvp_result = await db.execute(
        select(EventRSVP, Member)
        .join(Member, EventRSVP.member_id == Member.id)
        .where(
            and_(
                EventRSVP.event_id == event_id,
                Member.status.in_(("active", "recruit")),
            )
        )
        .order_by(EventRSVP.checked_in_at.desc().nullslast(), Member.last_name)
    )
    rsvp_rows = rsvp_result.all()

    # Get buddy pairs
    buddy_result = await db.execute(
        select(EventBuddyPair).where(EventBuddyPair.event_id == event_id)
    )
    buddy_pairs = buddy_result.scalars().all()

    # Build buddy lookup: member_id → pair
    buddy_map = {}
    for pair in buddy_pairs:
        if pair.member_a_id:
            buddy_map[pair.member_a_id] = pair
        if pair.member_b_id:
            buddy_map[pair.member_b_id] = pair

    # Get guard duty assignments
    guard_result = await db.execute(
        select(EventGuardDuty).where(EventGuardDuty.event_id == event_id)
    )
    guard_duties = guard_result.scalars().all()
    guard_map = {gd.member_id: gd for gd in guard_duties if gd.member_id}

    # Get vexillation assignments
    vex_assign_result = await db.execute(
        select(EventVexillationAssignment, EventVexillation)
        .join(EventVexillation, EventVexillationAssignment.vexillation_id == EventVexillation.id)
        .where(EventVexillationAssignment.event_id == event_id)
    )
    vex_assigns = vex_assign_result.all()
    vex_map = {row.EventVexillationAssignment.member_id: row.EventVexillation
               for row in vex_assigns if row.EventVexillationAssignment.member_id}

    # Build member id lookup for buddy names
    all_member_ids = set()
    for pair in buddy_pairs:
        if pair.member_a_id:
            all_member_ids.add(pair.member_a_id)
        if pair.member_b_id:
            all_member_ids.add(pair.member_b_id)

    member_name_map = {}
    if all_member_ids:
        m_result = await db.execute(select(Member).where(Member.id.in_(all_member_ids)))
        for m in m_result.scalars().all():
            member_name_map[m.id] = m

    # Assemble rows
    rows = []
    for rsvp, member in rsvp_rows:
        pair = buddy_map.get(member.id)
        buddy_name = None
        buddy_pair_id = None
        if pair:
            buddy_pair_id = pair.id
            other_id = pair.member_b_id if pair.member_a_id == member.id else pair.member_a_id
            if other_id and other_id in member_name_map:
                other = member_name_map[other_id]
                buddy_name = f"{other.last_name}, {other.first_name}"

        guard = guard_map.get(member.id)
        vex = vex_map.get(member.id)

        rows.append({
            "rsvp": rsvp,
            "member": member,
            "buddy_pair": pair,
            "buddy_pair_id": buddy_pair_id,
            "buddy_name": buddy_name,
            "guard_duty": guard,
            "vexillation": vex,
            "row_type": "member",
        })

    # Append checked-in guests
    guest_result = await db.execute(
        select(EventGuest, Member)
        .join(Member, EventGuest.sponsor_id == Member.id)
        .where(EventGuest.event_id == event_id)
        .order_by(EventGuest.checked_in_at.desc().nullslast(), EventGuest.last_name)
    )
    for guest, sponsor in guest_result.all():
        rows.append({
            "guest": guest,
            "sponsor": sponsor,
            "row_type": "guest",
        })

    return rows


async def _get_guard_slots(db, event_id: int) -> list[EventGuardSlot]:
    result = await db.execute(
        select(EventGuardSlot).where(EventGuardSlot.event_id == event_id)
        .order_by(EventGuardSlot.slot_number)
    )
    return result.scalars().all()


async def _get_guard_duties(db, event_id: int) -> list[EventGuardDuty]:
    result = await db.execute(
        select(EventGuardDuty).where(EventGuardDuty.event_id == event_id)
        .order_by(EventGuardDuty.slot_number)
    )
    return result.scalars().all()


async def _get_vexillations(db, event_id: int) -> list[EventVexillation]:
    result = await db.execute(
        select(EventVexillation)
        .options(selectinload(EventVexillation.assignments))
        .where(EventVexillation.event_id == event_id)
        .order_by(EventVexillation.created_at)
    )
    return result.scalars().all()


async def _get_member_map(db, event_id: int) -> dict[int, "Member"]:
    """Build a member_id → Member lookup for all members with RSVPs at this event."""
    result = await db.execute(
        select(Member)
        .join(EventRSVP, EventRSVP.member_id == Member.id)
        .where(EventRSVP.event_id == event_id)
    )
    return {m.id: m for m in result.scalars().all()}


async def _get_checked_in_members(db, event_id: int) -> list[Member]:
    result = await db.execute(
        select(Member)
        .join(EventRSVP, EventRSVP.member_id == Member.id)
        .where(and_(
            EventRSVP.event_id == event_id,
            EventRSVP.checked_in == True,
            Member.status.in_(("active", "recruit")),
        ))
        .order_by(Member.last_name)
    )
    return result.scalars().all()


# ─── QR SVG Helper ────────────────────────────────────────────────────────────

def _make_qr_svg(data: str) -> str:
    """Generate a QR code as an inline SVG string."""
    try:
        factory = qrcode.image.svg.SvgPathImage
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
            image_factory=factory,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf)
        buf.seek(0)
        return buf.read().decode("utf-8")
    except Exception:
        # Fallback: return empty string — template handles missing QR gracefully
        return ""
