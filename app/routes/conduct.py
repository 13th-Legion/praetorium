"""Code of Conduct violation history — view and record violations."""

import logging
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member
from app.models.conduct import ConductViolation
from app.constants import S1_ROLES

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conduct", tags=["conduct"])
templates = Jinja2Templates(directory="app/templates")

# Roles that can view and manage CoC violations (Command/Admin/S1 Lead only)
COC_ROLES = {"command", "admin", "s1_lead"}

ACTION_OPTIONS = [
    "Counseling",
    "Written Warning",
    "Non-Promotable",
    "Suspension",
    "Demotion",
    "Other",
]


def _can_manage(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & COC_ROLES)


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s or s.strip() == "":
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


@router.get("")
@require_auth
async def conduct_overview(request: Request, db: AsyncSession = Depends(get_db)):
    """Standalone page listing all CoC violations across all members."""
    user = get_current_user(request)
    if not _can_manage(user):
        raise HTTPException(status_code=403, detail="Command or S1 access required")

    # All violations joined with member names, newest first
    result = await db.execute(
        select(ConductViolation, Member)
        .join(Member, ConductViolation.member_id == Member.id)
        .order_by(ConductViolation.violation_date.desc())
    )
    rows = result.all()

    violations = []
    for v, m in rows:
        violations.append({
            "violation": v,
            "member": m,
        })

    # Count active violations
    active_count = sum(
        1 for item in violations
        if (item["violation"].end_date and item["violation"].end_date >= date.today())
        or (item["violation"].start_date and not item["violation"].end_date)
    )

    return templates.TemplateResponse("pages/conduct_overview.html", {
        "request": request,
        "user": user,
        "violations": violations,
        "active_count": active_count,
        "today": date.today(),
    })


@router.post("/{member_id}/add")
@require_auth
async def add_violation(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Record a new CoC violation for a member."""
    user = get_current_user(request)
    if not _can_manage(user):
        raise HTTPException(status_code=403, detail="Command or S1 access required")

    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    form = await request.form()

    violation_date = _parse_date(form.get("violation_date"))
    if not violation_date:
        return HTMLResponse(
            '<div style="color:#dc3545;font-size:13px;margin-top:8px;">⚠️ Violation date is required.</div>',
            status_code=422,
        )

    reason = (form.get("reason") or "").strip()
    if not reason:
        return HTMLResponse(
            '<div style="color:#dc3545;font-size:13px;margin-top:8px;">⚠️ Reason is required.</div>',
            status_code=422,
        )

    action_taken = (form.get("action_taken") or "").strip()
    if not action_taken:
        return HTMLResponse(
            '<div style="color:#dc3545;font-size:13px;margin-top:8px;">⚠️ Action taken is required.</div>',
            status_code=422,
        )

    start_date = _parse_date(form.get("start_date"))
    end_date = _parse_date(form.get("end_date"))

    duration_days = None
    if start_date and end_date:
        duration_days = (end_date - start_date).days

    notes = (form.get("notes") or "").strip() or None

    violation = ConductViolation(
        member_id=member_id,
        violation_date=violation_date,
        reason=reason,
        action_taken=action_taken,
        duration_days=duration_days,
        start_date=start_date,
        end_date=end_date,
        issued_by=user.get("username", "unknown"),
        notes=notes,
    )
    db.add(violation)
    await db.commit()

    log.info(f"CoC violation recorded for member {member_id} by {user.get('username')}: {action_taken}")

    # Return updated violation list via HTMX
    return await _render_violation_list(member_id, db, user)


@router.get("/{member_id}/list")
@require_auth
async def list_violations(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Fetch the violation history list (HTMX fragment)."""
    user = get_current_user(request)
    if not _can_manage(user):
        raise HTTPException(status_code=403, detail="Command or S1 access required")

    return await _render_violation_list(member_id, db, user)


async def _render_violation_list(member_id: int, db: AsyncSession, user: dict) -> HTMLResponse:
    """Render the violation list as an HTML fragment."""
    result = await db.execute(
        select(ConductViolation)
        .where(ConductViolation.member_id == member_id)
        .order_by(ConductViolation.violation_date.desc())
    )
    violations = result.scalars().all()

    if not violations:
        return HTMLResponse(
            '<p class="text-muted" style="font-size:13px;">No violations on record.</p>'
        )

    rows = []
    for v in violations:
        active = ""
        if v.end_date and v.end_date >= date.today():
            active = ' <span style="color:#dc3545;font-size:11px;font-weight:600;">ACTIVE</span>'
        elif v.end_date is None and v.start_date:
            active = ' <span style="color:#dc3545;font-size:11px;font-weight:600;">ACTIVE</span>'

        duration = ""
        if v.duration_days:
            duration = f" ({v.duration_days}d)"
        elif v.start_date and not v.end_date:
            duration = " (indefinite)"

        notes_html = ""
        if v.notes:
            notes_html = f'<div style="font-size:12px;color:#888;margin-top:2px;font-style:italic;">{v.notes}</div>'

        rows.append(f'''
            <div class="detail-row" style="align-items:flex-start;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
                <span class="detail-label" style="min-width:100px;font-size:13px;">{v.violation_date.strftime('%b %d, %Y')}</span>
                <span style="flex:1;">
                    <span style="color:#dc3545;font-weight:600;font-size:13px;">{v.action_taken}</span>{active}{duration}
                    <div style="font-size:12px;color:#ccc;margin-top:2px;">{v.reason}</div>
                    {notes_html}
                    <div style="font-size:11px;color:#666;margin-top:2px;">Issued by {v.issued_by}</div>
                </span>
            </div>
        ''')

    return HTMLResponse("".join(rows))


async def get_violations_for_profile(member_id: int, db: AsyncSession) -> list:
    """Fetch violations for display on profile page."""
    result = await db.execute(
        select(ConductViolation)
        .where(ConductViolation.member_id == member_id)
        .order_by(ConductViolation.violation_date.desc())
    )
    return result.scalars().all()
