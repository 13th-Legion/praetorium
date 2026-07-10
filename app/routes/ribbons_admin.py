"""Command ribbon-award interface — grant/revoke ribbons, decorations, tabs.

Lives under the Command navmenu. Mirrors the /api/awards dashboard pattern:
select a member, pick a catalog item, set device count + reason, grant. Also
lists a member's current ribbons with a revoke control.

All writes go to member_ribbons (source='manual').
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.constants import RANK_ABBR, AWARD_ROLES
from app.models.member import Member
from app.models.ribbons import RibbonCatalog, MemberRibbon

log = logging.getLogger("ribbons_admin")

router = APIRouter(prefix="/api/ribbons", tags=["ribbons"])
templates = Jinja2Templates(directory="app/templates")

SECTION_LABELS = {
    "dona": "Dona Militaria (Decorations)",
    "rack": "Ribbon Rack",
    "tab": "Tabs",
    "tenure": "Anni Stipendiorum (auto — tenure)",
}
SECTION_ORDER = ["dona", "rack", "tab", "tenure"]


def _can_award(user: dict) -> bool:
    return bool(set(user.get("roles", [])) & AWARD_ROLES)


def _awarder_name(user: dict, member=None) -> str:
    if member:
        rank = RANK_ABBR.get(member.rank_grade, "")
        return f"{rank} {member.last_name}".strip()
    return user.get("display_name", user.get("username", "unknown"))


@router.get("")
@require_auth
async def ribbon_admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Command interface: select member + grant ribbons/decorations/tabs."""
    user = get_current_user(request)
    if not _can_award(user):
        raise HTTPException(status_code=403, detail="Command or S1 access required")

    members = (await db.execute(
        select(Member).where(Member.status.in_(["active", "recruit"])).order_by(Member.last_name)
    )).scalars().all()
    for m in members:
        m.rank_display = RANK_ABBR.get(m.rank_grade, "")

    cat = (await db.execute(
        select(RibbonCatalog).where(RibbonCatalog.active.is_(True)).order_by(
            RibbonCatalog.section, RibbonCatalog.precedence
        )
    )).scalars().all()
    # group by section for the picker (tenure excluded — auto-computed)
    grouped = {s: [] for s in SECTION_ORDER}
    for c in cat:
        grouped.setdefault(c.section, []).append(c)

    return templates.TemplateResponse("pages/ribbon_admin.html", {
        "request": request,
        "user": user,
        "members": members,
        "grouped": grouped,
        "section_labels": SECTION_LABELS,
        "section_order": SECTION_ORDER,
    })


@router.get("/member/{member_id}/current", response_class=HTMLResponse)
@require_auth
async def member_current_ribbons(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """HTMX partial: a member's current ribbons with revoke controls."""
    user = get_current_user(request)
    if not _can_award(user):
        raise HTTPException(status_code=403)

    rows = (await db.execute(
        select(MemberRibbon, RibbonCatalog)
        .join(RibbonCatalog, RibbonCatalog.code == MemberRibbon.ribbon_code)
        .where(MemberRibbon.member_id == member_id)
        .order_by(RibbonCatalog.section, RibbonCatalog.precedence)
    )).all()

    if not rows:
        return HTMLResponse('<p style="color:#888;font-size:13px;">No ribbons awarded yet.</p>')

    html = '<div style="display:flex;flex-direction:column;gap:6px;">'
    for mr, c in rows:
        dev = f' <span style="color:#f4d878;">+{mr.device_count} dev</span>' if mr.device_count else ''
        html += (
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'gap:8px;padding:6px 8px;background:rgba(255,255,255,.04);border-radius:4px;">'
            f'<span style="font-size:12px;color:#e8e6dc;">'
            f'<img src="/static/img/ribbons/{c.image}" style="height:18px;vertical-align:middle;margin-right:6px;">'
            f'{c.name}{dev} <span style="color:#888;">({mr.source})</span></span>'
            f'<button hx-post="/api/ribbons/revoke" hx-vals=\'{{"member_id":{member_id},"code":"{c.code}"}}\' '
            f'hx-target="#current-ribbons" hx-confirm="Revoke {c.name}?" '
            f'style="padding:2px 8px;background:#7a2020;color:#fff;border:none;border-radius:3px;font-size:11px;cursor:pointer;">Revoke</button>'
            f'</div>'
        )
    html += '</div>'
    return HTMLResponse(html)


@router.post("/grant")
@require_auth
async def grant_ribbon(request: Request, db: AsyncSession = Depends(get_db)):
    """Grant (or update device count on) a ribbon/decoration/tab."""
    user = get_current_user(request)
    if not _can_award(user):
        raise HTTPException(status_code=403)

    form = await request.form()
    member_id = int(form.get("member_id", 0) or 0)
    code = (form.get("code") or "").strip()
    device_count = int(form.get("device_count", 0) or 0)
    reason = (form.get("reason") or "").strip()

    if not member_id or not code:
        raise HTTPException(status_code=400, detail="Select a member and a ribbon")

    cat = (await db.execute(select(RibbonCatalog).where(RibbonCatalog.code == code))).scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=400, detail="Unknown ribbon code")
    if cat.section == "tenure":
        return HTMLResponse('<p style="color:#ef6c00;">⚠️ Tenure discs are auto-computed from join date, not granted.</p>')

    # clamp device count
    if cat.max_devices and device_count > cat.max_devices:
        device_count = cat.max_devices
    if not cat.device_increment:
        device_count = 0
    device_count = max(0, device_count)

    awarder = (await db.execute(select(Member).where(Member.nc_username == user["username"]))).scalar_one_or_none()
    awarder_name = _awarder_name(user, awarder)

    existing = (await db.execute(
        select(MemberRibbon).where(
            MemberRibbon.member_id == member_id, MemberRibbon.ribbon_code == code
        )
    )).scalar_one_or_none()

    if existing:
        existing.device_count = device_count
        if reason:
            existing.reason = reason
        existing.awarded_by = awarder_name
        msg = f"Updated {cat.name}"
    else:
        db.add(MemberRibbon(
            member_id=member_id, ribbon_code=code, device_count=device_count,
            reason=reason or None, awarded_by=awarder_name, source="manual",
        ))
        msg = f"Awarded {cat.name}"
    await db.commit()
    return HTMLResponse(f'<p style="color:#2e7d32;font-weight:600;">✅ {msg}</p>')


@router.post("/revoke")
@require_auth
async def revoke_ribbon(request: Request, db: AsyncSession = Depends(get_db)):
    """Revoke a ribbon from a member (returns the refreshed current-ribbons list)."""
    user = get_current_user(request)
    if not _can_award(user):
        raise HTTPException(status_code=403)

    form = await request.form()
    member_id = int(form.get("member_id", 0) or 0)
    code = (form.get("code") or "").strip()

    row = (await db.execute(
        select(MemberRibbon).where(
            MemberRibbon.member_id == member_id, MemberRibbon.ribbon_code == code
        )
    )).scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()

    # return refreshed list
    return await member_current_ribbons(request, member_id, db)
