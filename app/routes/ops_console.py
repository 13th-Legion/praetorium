"""Event Operations Console routes — PP-070.

Provides the live ops console for any event:
  - QR check-in display + HMAC token validation
  - Live roster (HTMX polling)
  - Battle buddy pairing
  - Guard duty slots + assignment
  - Vexillation (mission team) management
  - Walk-in guest management
  - Manual check-in / un-check-in by S1
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
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_, delete, or_
from sqlalchemy.orm import selectinload

from app.auth import require_auth, require_role, get_current_user
from app.clock import now_ct
from app import database
from app.models.events import (
    Event, EventRSVP, EventGuest, EventBuddyPair,
    EventGuardSlot, EventGuardDuty, EventVexillation, EventVexillationAssignment,
    EventDutyAssignment,
)
from app.models.member import Member
from app.models.s2_intel import S2ChallengePassword
from app.services import teams as teams_svc
from config import get_settings

router = APIRouter(tags=["ops-console"])
templates = Jinja2Templates(directory="app/templates")

# ─── CDT Filter (match events.py convention) ─────────────────────────────────

from zoneinfo import ZoneInfo
_CDT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


def _fmt_ct_stored(dt):
    """Tag an already-naive-CT datetime with CT tz WITHOUT converting.
    For event date_start/date_end (stored naive wall-clock CT)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=_CDT) if dt.tzinfo is None else dt.astimezone(_CDT)


def _to_cdt(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_CDT)


templates.env.filters["cdt"] = _to_cdt
templates.env.filters["cdt_stored"] = _fmt_ct_stored

# ─── Constants ────────────────────────────────────────────────────────────────

TACTICAL_CATEGORIES = {"ftx", "mcftx", "training_course"}
OPS_ROLES = ("s1", "s3", "command", "admin")
DUTY_SUGGESTED_LABELS = ("KP", "Latrine")
S1_CMD_ROLES = ("s1", "command", "admin")
S1_S2_CMD_ROLES = ("s1", "s2", "command", "admin")
S3_CMD_ROLES = ("s3", "command", "admin")
S1_S3_CMD_ROLES = ("s1", "s3", "command", "admin")

TOKEN_WINDOW_SECONDS = 900  # 15 min rotation


def _form_flag(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "on", "yes", "y"}


def _normalize_duty_label(label: str) -> str:
    cleaned = (label or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Duty label is required")
    if len(cleaned) > 32:
        raise HTTPException(status_code=400, detail="Duty label must be 32 characters or fewer")
    return cleaned


def _ops_flags(user: dict, display_mode: str = "normal") -> dict:
    return {
        "display_mode": display_mode,
        "can_checkin": _user_has_role(user, *OPS_ROLES),
        "can_add_guest": _user_has_role(user, *S1_CMD_ROLES),
        "can_buddy": _user_has_role(user, *S1_CMD_ROLES),
        "can_guard": _user_has_role(user, *S1_S2_CMD_ROLES),
        "can_vex_create": _user_has_role(user, *S3_CMD_ROLES),
        "can_vex_assign": _user_has_role(user, *S1_S3_CMD_ROLES),
        "can_immunes": _user_has_role(user, *S1_S3_CMD_ROLES),
        "can_duty": _user_has_role(user, *S1_S3_CMD_ROLES),
        "duty_labels": DUTY_SUGGESTED_LABELS,
    }


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

    async with database.async_session() as db:
        event = await _get_event_or_404(db, event_id)
        roster_rows = await _build_roster(db, event)
        guard_slots = await _get_guard_slots(db, event_id)
        guard_duties = await _get_guard_duties(db, event_id)
        vexillations = await _get_vexillations(db, event_id)
        checked_in_members = await _get_checked_in_members(db, event_id)
        member_map = await _get_member_map(db, event_id)
        guest_map = await _get_guest_map(db, event_id)
        duty_assignments = await _get_duty_assignments(db, event_id)
        now = now_ct()
        event_sets = (await db.execute(
            select(S2ChallengePassword)
            .where(S2ChallengePassword.event_id == event_id, S2ChallengePassword.active.is_(True))
            .order_by(S2ChallengePassword.created_at)
        )).scalars().all()
        # Standing sets (no event) that are inside their window, if they have one.
        standing_sets = (await db.execute(
            select(S2ChallengePassword)
            .where(
                S2ChallengePassword.event_id.is_(None),
                S2ChallengePassword.active.is_(True),
                or_(S2ChallengePassword.valid_from.is_(None), S2ChallengePassword.valid_from <= now),
                or_(S2ChallengePassword.valid_until.is_(None), S2ChallengePassword.valid_until >= now),
            )
            .order_by(S2ChallengePassword.created_at)
        )).scalars().all()
        challenge_sets = list(event_sets) + list(standing_sets)
        sponsor_ids = {g.sponsor_id for g in guest_map.values()} - set(member_map)
        if sponsor_ids:
            extra = await db.execute(select(Member).where(Member.id.in_(sponsor_ids)))
            for m in extra.scalars().all():
                member_map[m.id] = m
    team_options = await teams_svc.team_options()

    show_tactical = event.category in TACTICAL_CATEGORIES
    display_mode = request.query_params.get("display", "normal")  # "tv" or "normal"

    # Generate QR code SVG
    window = _qr_rotation_window()
    token = _generate_qr_token(event_id, window, settings.secret_key)
    qr_url = str(request.base_url).rstrip("/") + f"/events/{event_id}/checkin?token={token}"
    qr_svg = _make_qr_svg(qr_url)

    # Next refresh time
    next_refresh_unix = (window + 1) * TOKEN_WINDOW_SECONDS
    next_refresh = datetime.fromtimestamp(next_refresh_unix, tz=_CDT)

    return templates.TemplateResponse("pages/ops_console.html", {
        "request": request,
        "user": user,
        "event": event,
        "roster": roster_rows,
        "guard_slots": guard_slots,
        "guard_duties": guard_duties,
        "vexillations": vexillations,
        "checked_in_members": checked_in_members,
        "guest_map": guest_map,
        "duty_assignments": duty_assignments,
        "challenge_sets": challenge_sets,
        "team_options": team_options,
        "qr_svg": qr_svg,
        "qr_url": qr_url,
        "next_refresh": next_refresh,
        "show_tactical": show_tactical,
        "member_map": member_map,
        **_ops_flags(user, display_mode),
    })


# ─── QR Code endpoint ─────────────────────────────────────────────────────────

@router.get("/events/{event_id}/ops/qr", response_class=Response)
@require_role(*OPS_ROLES)
async def ops_qr_code(request: Request, event_id: int):
    """Return a QR code PNG image for the current check-in token."""
    user = get_current_user(request)
    settings = get_settings()

    async with database.async_session() as db:
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

    async with database.async_session() as db:
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

    async with database.async_session() as db:
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

    async with database.async_session() as db:
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
        **_ops_flags(user, request.query_params.get("display", "normal")),
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

    async with database.async_session() as db:
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
            "guard_slots": guard_slots,
            "vexillations": vexillations,
            **_ops_flags(user, request.query_params.get("display", "normal")),
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

    async with database.async_session() as db:
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
    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


@router.post("/events/{event_id}/ops/uncheckin", response_class=HTMLResponse)
@require_role(*OPS_ROLES)
async def manual_uncheckin(
    request: Request,
    event_id: int,
    member_id: int = Form(...),
):
    """Undo a check-in. Also clears official attendance so Finalize cannot restore it.

    Works after finalize: reverses this-event auto TRADOC credits and recomputes
    ftx_count / last_ftx for that member. Does not unfinalize the event.
    """
    async with database.async_session() as db:
        event = await _get_event_or_404(db, event_id)
        result = await db.execute(
            select(EventRSVP).where(
                and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member_id)
            )
        )
        rsvp = result.scalar_one_or_none()
        if not rsvp or not (rsvp.checked_in or rsvp.attended):
            return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)

        from app.services import attendance as attendance_svc
        await attendance_svc.revoke_rsvp_attendance(db, event, rsvp)
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


# ─── Immunes (event-scoped extra-duty exemption) ───────────────────────────────

@router.post("/events/{event_id}/ops/immunes", response_class=HTMLResponse)
@require_role(*S1_S3_CMD_ROLES)
async def toggle_immunes(
    request: Request,
    event_id: int,
    member_id: int = Form(...),
    immunes: str = Form(""),
):
    """Toggle the event-scoped Immunes flag. Existing assignments are left in place."""
    user = get_current_user(request)

    async with database.async_session() as db:
        event = await _get_event_or_404(db, event_id)
        result = await db.execute(
            select(EventRSVP).where(
                and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member_id)
            )
        )
        rsvp = result.scalar_one_or_none()
        if not rsvp:
            raise HTTPException(status_code=404, detail="RSVP not found")
        rsvp.immunes = _form_flag(immunes) if immunes != "" else (not rsvp.immunes)
        await db.commit()

        roster_rows = await _build_roster(db, event)
        guard_slots = await _get_guard_slots(db, event_id)
        vexillations = await _get_vexillations(db, event_id)

    return templates.TemplateResponse("partials/ops_roster.html", {
        "request": request,
        "user": user,
        "event": event,
        "roster": roster_rows,
        "guard_slots": guard_slots,
        "vexillations": vexillations,
        "show_tactical": event.category in TACTICAL_CATEGORIES,
        **_ops_flags(user, request.query_params.get("display", "normal")),
    })


# ─── Duty team (event-scoped extra duties) ────────────────────────────────────

@router.post("/events/{event_id}/ops/duty/assign", response_class=HTMLResponse)
@require_role(*S1_S3_CMD_ROLES)
async def assign_duty(
    request: Request,
    event_id: int,
    member_id: int = Form(...),
    duty_label: str = Form(...),
    override_immunes: str = Form(""),
):
    """Add one checked-in member to the event duty team. Immunes need override."""
    user = get_current_user(request)
    username = user.get("username", "unknown")
    label = _normalize_duty_label(duty_label)

    async with database.async_session() as db:
        await _get_event_or_404(db, event_id)
        rsvp_result = await db.execute(
            select(EventRSVP).where(
                and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member_id)
            )
        )
        rsvp = rsvp_result.scalar_one_or_none()
        if not rsvp or not rsvp.checked_in:
            raise HTTPException(status_code=400, detail="Member is not checked in")
        if rsvp.immunes and not _form_flag(override_immunes):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Member is immunes (exempt from extra duties). "
                    "Resubmit with override_immunes=1 to assign anyway."
                ),
            )
        existing = await db.execute(
            select(EventDutyAssignment).where(
                and_(
                    EventDutyAssignment.event_id == event_id,
                    EventDutyAssignment.member_id == member_id,
                )
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            row.duty_label = label
            row.source = "ad_hoc"
            row.geo_team_name = None
            row.assigned_by = username
        else:
            db.add(EventDutyAssignment(
                event_id=event_id,
                member_id=member_id,
                duty_label=label,
                source="ad_hoc",
                assigned_by=username,
                created_at=datetime.utcnow(),
            ))
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


@router.post("/events/{event_id}/ops/duty/from-team", response_class=HTMLResponse)
@require_role(*S1_S3_CMD_ROLES)
async def assign_duty_from_team(
    request: Request,
    event_id: int,
    team_name: str = Form(...),
    duty_label: str = Form(...),
):
    """Put every checked-in member of a geographic/HQ team on extra duty.

    Immunes are skipped. Permanent Member.team is not written.
    """
    user = get_current_user(request)
    username = user.get("username", "unknown")
    label = _normalize_duty_label(duty_label)
    team_name = (team_name or "").strip()
    known = set(await teams_svc.team_options())
    if team_name not in known:
        raise HTTPException(status_code=400, detail="Unknown team")

    async with database.async_session() as db:
        await _get_event_or_404(db, event_id)
        immunes_ids = await _immunes_member_ids(db, event_id)
        already = await db.execute(
            select(EventDutyAssignment.member_id).where(
                EventDutyAssignment.event_id == event_id
            )
        )
        already_ids = {row[0] for row in already.all()}
        result = await db.execute(
            select(Member.id)
            .join(EventRSVP, EventRSVP.member_id == Member.id)
            .where(and_(
                EventRSVP.event_id == event_id,
                EventRSVP.checked_in == True,
                Member.team == team_name,
                Member.status.in_(("active", "recruit")),
            ))
        )
        for (member_id,) in result.all():
            if member_id in immunes_ids or member_id in already_ids:
                continue
            db.add(EventDutyAssignment(
                event_id=event_id,
                member_id=member_id,
                duty_label=label,
                source="geo_team",
                geo_team_name=team_name,
                assigned_by=username,
                created_at=datetime.utcnow(),
            ))
        await db.commit()

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


@router.delete("/events/{event_id}/ops/duty/{assignment_id}", response_class=HTMLResponse)
@require_role(*S1_S3_CMD_ROLES)
async def unassign_duty(request: Request, event_id: int, assignment_id: int):
    async with database.async_session() as db:
        result = await db.execute(
            select(EventDutyAssignment).where(
                and_(
                    EventDutyAssignment.id == assignment_id,
                    EventDutyAssignment.event_id == event_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Duty assignment not found")
        await db.delete(row)
        await db.commit()
    return HTMLResponse(content="", status_code=200)


@router.post("/events/{event_id}/ops/duty/{assignment_id}/delete", response_class=HTMLResponse)
@require_role(*S1_S3_CMD_ROLES)
async def unassign_duty_post(request: Request, event_id: int, assignment_id: int):
    async with database.async_session() as db:
        result = await db.execute(
            select(EventDutyAssignment).where(
                and_(
                    EventDutyAssignment.id == assignment_id,
                    EventDutyAssignment.event_id == event_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Duty assignment not found")
        await db.delete(row)
        await db.commit()
    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


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

    async with database.async_session() as db:
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

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


@router.delete("/events/{event_id}/ops/buddy/{pair_id}", response_class=HTMLResponse)
@require_role(*S1_CMD_ROLES)
async def unpair_buddy(request: Request, event_id: int, pair_id: int):
    """Remove a battle buddy pairing (HTMX DELETE)."""
    async with database.async_session() as db:
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
    async with database.async_session() as db:
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
    async with database.async_session() as db:
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
    override_immunes: str = Form(""),
):
    """Assign a member or guest to a guard duty slot.

    Immunes members are not silently assigned: the client must send
    override_immunes=1 after an explicit confirm. Command is not hard-blocked.
    """
    user = get_current_user(request)
    username = user.get("username", "unknown")

    async with database.async_session() as db:
        await _get_event_or_404(db, event_id)

        if member_id:
            rsvp_result = await db.execute(
                select(EventRSVP).where(
                    and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member_id)
                )
            )
            rsvp = rsvp_result.scalar_one_or_none()
            if rsvp and rsvp.immunes and not _form_flag(override_immunes):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Member is immunes (exempt from extra duties). "
                        "Resubmit with override_immunes=1 to assign anyway."
                    ),
                )

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

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


@router.delete("/events/{event_id}/ops/guard/{assignment_id}", response_class=HTMLResponse)
@require_role(*S1_S2_CMD_ROLES)
async def unassign_guard(request: Request, event_id: int, assignment_id: int):
    """Remove a guard duty assignment (HTMX DELETE)."""
    async with database.async_session() as db:
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
    async with database.async_session() as db:
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

    async with database.async_session() as db:
        await _get_event_or_404(db, event_id)

        # Get slots
        slot_result = await db.execute(
            select(EventGuardSlot).where(EventGuardSlot.event_id == event_id)
            .order_by(EventGuardSlot.slot_number)
        )
        slots = slot_result.scalars().all()
        if not slots:
            raise HTTPException(status_code=400, detail="No guard slots configured")

        # Checked-in, not immunes, not already assigned. Guests appended in PP-325.
        targets = await _guard_auto_assign_targets(db, event_id)

        # Distribute
        for i, (member_id, guest_id) in enumerate(targets):
            slot = slots[i % len(slots)]
            duty = EventGuardDuty(
                event_id=event_id,
                slot_id=slot.id,
                slot_number=slot.slot_number,
                slot_label=slot.slot_label,
                member_id=member_id,
                guest_id=guest_id,
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

    async with database.async_session() as db:
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
    async with database.async_session() as db:
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

    async with database.async_session() as db:
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
        async with database.async_session() as db:
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

    async with database.async_session() as db:
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

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


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

    async with database.async_session() as db:
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
    async with database.async_session() as db:
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

    async with database.async_session() as db:
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

    return RedirectResponse(url=f"/events/{event_id}/ops", status_code=303)


# ─── Guard Config Partial ─────────────────────────────────────────────────────

@router.get("/events/{event_id}/ops/guard/config", response_class=HTMLResponse)
@require_role(*S1_S2_CMD_ROLES)
async def ops_guard_config(request: Request, event_id: int):
    """HTMX partial: guard slot configuration panel."""
    user = get_current_user(request)

    async with database.async_session() as db:
        event = await _get_event_or_404(db, event_id)
        guard_slots = await _get_guard_slots(db, event_id)
        guard_duties = await _get_guard_duties(db, event_id)
        member_map = await _get_member_map(db, event_id)
        guest_map = await _get_guest_map(db, event_id)
        sponsor_ids = {g.sponsor_id for g in guest_map.values()} - set(member_map)
        if sponsor_ids:
            extra = await db.execute(select(Member).where(Member.id.in_(sponsor_ids)))
            for m in extra.scalars().all():
                member_map[m.id] = m

    return templates.TemplateResponse("partials/ops_guard_config.html", {
        "request": request,
        "user": user,
        "event": event,
        "guard_slots": guard_slots,
        "guard_duties": guard_duties,
        "member_map": member_map,
        "guest_map": guest_map,
    })


# ─── Vexillation Summary Partial ──────────────────────────────────────────────

@router.get("/events/{event_id}/ops/vexillations", response_class=HTMLResponse)
@require_role(*OPS_ROLES)
async def ops_vex_summary(request: Request, event_id: int):
    """HTMX partial: vexillation summary with field status toggles."""
    user = get_current_user(request)

    async with database.async_session() as db:
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
        .order_by(
            EventRSVP.checked_in_at.desc().nullslast(),
            EventRSVP.no_show.desc(),
            Member.last_name,
        )
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
    guest_guard_map = {gd.guest_id: gd for gd in guard_duties if gd.guest_id}

    duty_result = await db.execute(
        select(EventDutyAssignment).where(EventDutyAssignment.event_id == event_id)
    )
    duty_map = {d.member_id: d for d in duty_result.scalars().all()}

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
            "duty": duty_map.get(member.id),
            "immunes": bool(rsvp.immunes),
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
            "guard_duty": guest_guard_map.get(guest.id),
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


async def _get_guest_map(db, event_id: int) -> dict[int, EventGuest]:
    result = await db.execute(
        select(EventGuest).where(EventGuest.event_id == event_id)
    )
    return {g.id: g for g in result.scalars().all()}


async def _get_duty_assignments(db, event_id: int) -> list[EventDutyAssignment]:
    result = await db.execute(
        select(EventDutyAssignment).where(EventDutyAssignment.event_id == event_id)
        .order_by(EventDutyAssignment.created_at)
    )
    return result.scalars().all()


async def _immunes_member_ids(db, event_id: int) -> set[int]:
    result = await db.execute(
        select(EventRSVP.member_id).where(
            and_(EventRSVP.event_id == event_id, EventRSVP.immunes == True)
        )
    )
    return {row[0] for row in result.all()}


async def _guard_auto_assign_targets(
    db, event_id: int
) -> list[tuple[Optional[int], Optional[int]]]:
    """People eligible for guard auto-assign: (member_id, guest_id).

    Members first (checked-in, not immunes, not already assigned), then
    checked-in guests appended (ordered by last name, first name). Guests
    are not immunes in v1.
    """
    assigned_members = await db.execute(
        select(EventGuardDuty.member_id).where(
            and_(EventGuardDuty.event_id == event_id, EventGuardDuty.member_id.isnot(None))
        )
    )
    already_assigned_members = {row[0] for row in assigned_members.all()}
    immunes_ids = await _immunes_member_ids(db, event_id)

    checkin_result = await db.execute(
        select(EventRSVP.member_id).where(
            and_(EventRSVP.event_id == event_id, EventRSVP.checked_in == True)
        )
    )
    targets: list[tuple[Optional[int], Optional[int]]] = []
    for (member_id,) in checkin_result.all():
        if member_id in already_assigned_members or member_id in immunes_ids:
            continue
        targets.append((member_id, None))

    assigned_guests = await db.execute(
        select(EventGuardDuty.guest_id).where(
            and_(EventGuardDuty.event_id == event_id, EventGuardDuty.guest_id.isnot(None))
        )
    )
    already_assigned_guests = {row[0] for row in assigned_guests.all()}
    guest_result = await db.execute(
        select(EventGuest).where(
            and_(
                EventGuest.event_id == event_id,
                EventGuest.checked_in_at.isnot(None),
            )
        ).order_by(EventGuest.last_name, EventGuest.first_name)
    )
    for guest in guest_result.scalars().all():
        if guest.id in already_assigned_guests:
            continue
        targets.append((None, guest.id))
    return targets


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
