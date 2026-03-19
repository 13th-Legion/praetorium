"""Direct award/grant routes — Command/S1 can award certs, TRADOC, and gladii."""

from datetime import datetime

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member
from app.models.training import TradocItem, MemberTradoc, Certification, MemberCertification
from app.models.awards import MemberAward

router = APIRouter(prefix="/api/awards", tags=["awards"])
templates = Jinja2Templates(directory="app/templates")

from app.constants import RANK_ABBR, AWARD_ROLES


def _can_award(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & AWARD_ROLES)


def _get_awarder_name(user: dict, member=None) -> str:
    """Get display name for the person granting the award."""
    if member:
        rank = RANK_ABBR.get(member.rank_grade, "")
        return f"{rank} {member.last_name}".strip()
    return user.get("display_name", user.get("username", "unknown"))


# ─── Award Dashboard ────────────────────────────────────────────────────────

@router.get("")
@require_auth
async def award_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Main award page — select member, then grant certs/TRADOC/gladii."""
    user = get_current_user(request)
    if not _can_award(user):
        raise HTTPException(status_code=403, detail="Command or S1 access required")

    # Get all active members for dropdown
    result = await db.execute(
        select(Member)
        .where(Member.status.in_(["active", "recruit"]))
        .order_by(Member.last_name)
    )
    members = []
    for m in result.scalars().all():
        rank = RANK_ABBR.get(m.rank_grade, "")
        m.rank_display = rank
        members.append(m)

    # Get certs and TRADOC items
    comms_sort = case((Certification.category == "communications", 1), else_=0)
    cert_result = await db.execute(
        select(Certification).order_by(comms_sort, Certification.sort_order, Certification.name)
    )
    certs = cert_result.scalars().all()

    tradoc_result = await db.execute(select(TradocItem).order_by(TradocItem.sort_order))
    tradoc_items = tradoc_result.scalars().all()

    return templates.TemplateResponse("pages/award_dashboard.html", {
        "request": request,
        "user": user,
        "members": members,
        "certs": certs,
        "tradoc_items": tradoc_items,
    })


# ─── Get member's current awards/certs/TRADOC (HTMX partial) ────────────────

@router.get("/member/{member_id}/status", response_class=HTMLResponse)
@require_auth
async def member_award_status(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Return current training/cert/award status for a member (HTMX partial)."""
    user = get_current_user(request)
    if not _can_award(user):
        raise HTTPException(status_code=403)

    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        return HTMLResponse('<p style="color:#c62828;">Member not found</p>')

    # Current TRADOC
    tradoc_done = await db.execute(
        select(MemberTradoc.item_id).where(MemberTradoc.member_id == member_id)
    )
    done_ids = {row[0] for row in tradoc_done.all()}

    # Current certs
    certs_done = await db.execute(
        select(MemberCertification.certification_id).where(MemberCertification.member_id == member_id)
    )
    cert_ids = {row[0] for row in certs_done.all()}

    # Current gladii
    gladii_result = await db.execute(
        select(MemberAward).where(MemberAward.member_id == member_id).order_by(MemberAward.awarded_at.desc())
    )
    gladii = gladii_result.scalars().all()

    rank = RANK_ABBR.get(member.rank_grade, "")
    name = f"{rank} {member.last_name}".strip()

    html = f'<div style="margin-bottom:12px;font-weight:600;font-size:15px;">{name}</div>'
    html += f'<div style="font-size:12px;color:#aaa;margin-bottom:8px;">TRADOC: {len(done_ids)}/21 · Certs: {len(cert_ids)} · Gladii: {len(gladii)}</div>'
    html += f'<input type="hidden" id="done-tradoc" value="{",".join(str(i) for i in done_ids)}">'
    html += f'<input type="hidden" id="done-certs" value="{",".join(str(i) for i in cert_ids)}">'

    if gladii:
        html += '<div style="margin-top:8px;">'
        for g in gladii:
            html += f'<div style="font-size:12px;color:#d4a537;">⚔️ {g.award_name} — {g.reason or "No reason"} ({g.awarded_at.strftime("%b %d, %Y")})</div>'
        html += '</div>'

    return HTMLResponse(html)


# ─── Grant TRADOC Sign-offs ──────────────────────────────────────────────────

@router.post("/tradoc")
@require_auth
async def grant_tradoc(request: Request, db: AsyncSession = Depends(get_db)):
    """Directly sign off TRADOC items for a member."""
    user = get_current_user(request)
    if not _can_award(user):
        raise HTTPException(status_code=403)

    form = await request.form()
    member_id = int(form.get("member_id", 0))
    item_ids = [int(v) for v in form.getlist("tradoc_ids") if v]

    if not member_id or not item_ids:
        raise HTTPException(status_code=400, detail="Select a member and at least one item")

    # Get awarder name
    awarder_result = await db.execute(select(Member).where(Member.nc_username == user["username"]))
    awarder = awarder_result.scalar_one_or_none()
    awarder_name = _get_awarder_name(user, awarder)

    created = 0
    for item_id in item_ids:
        # Skip if already signed off
        existing = await db.execute(
            select(MemberTradoc).where(
                MemberTradoc.member_id == member_id,
                MemberTradoc.item_id == item_id,
            )
        )
        if existing.scalar_one_or_none():
            continue

        signoff = MemberTradoc(
            member_id=member_id,
            item_id=item_id,
            signed_off_by=awarder_name,
            notes="Direct sign-off",
        )
        db.add(signoff)
        created += 1

    await db.commit()
    return HTMLResponse(f'<p style="color:#2e7d32;font-weight:600;">✅ {created} TRADOC item{"s" if created != 1 else ""} signed off</p>')


# ─── Grant Certifications ───────────────────────────────────────────────────

@router.post("/cert")
@require_auth
async def grant_cert(request: Request, db: AsyncSession = Depends(get_db)):
    """Directly award a certification to a member."""
    user = get_current_user(request)
    if not _can_award(user):
        raise HTTPException(status_code=403)

    form = await request.form()
    member_id = int(form.get("member_id", 0))
    cert_id = int(form.get("cert_id", 0))

    if not member_id or not cert_id:
        raise HTTPException(status_code=400, detail="Select a member and certification")

    # Check if already earned
    existing = await db.execute(
        select(MemberCertification).where(
            MemberCertification.member_id == member_id,
            MemberCertification.certification_id == cert_id,
        )
    )
    if existing.scalar_one_or_none():
        return HTMLResponse('<p style="color:#ef6c00;">⚠️ Already has this certification</p>')

    awarder_result = await db.execute(select(Member).where(Member.nc_username == user["username"]))
    awarder = awarder_result.scalar_one_or_none()
    awarder_name = _get_awarder_name(user, awarder)

    cert_award = MemberCertification(
        member_id=member_id,
        certification_id=cert_id,
        awarded_by=awarder_name,
        notes="Direct award",
    )
    db.add(cert_award)
    await db.commit()

    return HTMLResponse('<p style="color:#2e7d32;font-weight:600;">✅ Certification awarded</p>')


# ─── Grant Gladius ───────────────────────────────────────────────────────────

@router.post("/gladius")
@require_auth
async def grant_gladius(request: Request, db: AsyncSession = Depends(get_db)):
    """Award a Gladius (or other unit award) to a member."""
    user = get_current_user(request)
    if not _can_award(user):
        raise HTTPException(status_code=403)

    form = await request.form()
    member_id = int(form.get("member_id", 0))
    award_name = form.get("award_name", "Gladius").strip()
    reason = form.get("reason", "").strip()

    if not member_id:
        raise HTTPException(status_code=400, detail="Select a member")
    if not reason:
        raise HTTPException(status_code=400, detail="Reason is required for award citations")

    awarder_result = await db.execute(select(Member).where(Member.nc_username == user["username"]))
    awarder = awarder_result.scalar_one_or_none()
    awarder_name = _get_awarder_name(user, awarder)

    award = MemberAward(
        member_id=member_id,
        award_name=award_name,
        reason=reason,
        awarded_by=awarder_name,
    )
    db.add(award)
    await db.commit()

    return HTMLResponse(f'<p style="color:#2e7d32;font-weight:600;">✅ {award_name} awarded</p>')
