"""S6 Comms dashboard — EmComm Plan, channel/freq reference, net schedule, radio inventory.

PP-383. Visibility: Command, S6 shop head, HQ Team Lead. Edit: Command, S6 shop head.
"""
from __future__ import annotations

import html as _html
import io
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_guest, require_auth
from app.clock import now_ct
from app.database import get_db
from app.models.member import Member
from app.models.s6_comms import S6Channel, S6EmcommPlan, S6NetSchedule, S6Radio
from app.models.s6_crypto import S6ShackleKey
from app.services.ranks import abbr_map

router = APIRouter(prefix="/api/s6", tags=["s6-admin"])
templates = Jinja2Templates(directory="app/templates")


async def _current_member(request: Request, db: AsyncSession) -> Member | None:
    user = get_current_user(request)
    if not user:
        return None
    uname = user.get("username")
    res = await db.execute(select(Member).where(Member.nc_username == uname))
    return res.scalar_one_or_none()


def _is_s6_head(member: Member | None) -> bool:
    """True if the member's billets carry 'S6: … (Lead)'."""
    if not member or not member.primary_billet:
        return False
    for part in member.primary_billet.split(","):
        part = part.strip()
        if re.match(r"S6\s*:", part) and "(Lead)" in part:
            return True
    return False


def _can_view(user: dict | None, member: Member | None) -> bool:
    """Command, admin, HQ Team Lead (leader), or S6 shop head."""
    if not user or is_guest(user):
        return False
    roles = set(user.get("roles", []))
    if roles & {"command", "admin", "leader"}:
        return True
    return _is_s6_head(member)


def _can_edit(user: dict | None, member: Member | None) -> bool:
    """Command, admin, or S6 shop head."""
    if not user or is_guest(user):
        return False
    roles = set(user.get("roles", []))
    if roles & {"command", "admin"}:
        return True
    return _is_s6_head(member)


async def _comms_roster(db: AsyncSession) -> list[Member]:
    """Active/recruit members holding a HAM and/or GMRS license."""
    res = await db.execute(
        select(Member)
        .where(
            Member.status.in_(["active", "recruit"]),
            or_(
                (Member.ham_callsign.isnot(None)) & (Member.ham_callsign != ""),
                (Member.gmrs_callsign.isnot(None)) & (Member.gmrs_callsign != ""),
            ),
        )
        .order_by(Member.last_name, Member.first_name)
    )
    return list(res.scalars().all())


@router.get("", response_class=HTMLResponse)
@require_auth
async def s6_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_view(user, member):
        raise HTTPException(status_code=403, detail="Restricted")
    plan = (await db.execute(select(S6EmcommPlan).where(S6EmcommPlan.id == 1))).scalar_one_or_none()
    channel_count = (await db.execute(select(func.count(S6Channel.id)))).scalar() or 0
    net_count = (await db.execute(select(func.count(S6NetSchedule.id)))).scalar() or 0
    radio_total = (await db.execute(select(func.count(S6Radio.id)))).scalar() or 0
    radio_out = (await db.execute(select(func.count(S6Radio.id)).where(S6Radio.status == "checked_out"))).scalar() or 0
    roster = await _comms_roster(db)
    shackle = (await db.execute(select(S6ShackleKey).order_by(S6ShackleKey.id.desc()).limit(1))).scalar_one_or_none()
    if plan and plan.content:
        plan_status = "Live"
    elif plan and plan.draft_content:
        plan_status = "Draft"
    else:
        plan_status = "None"
    return templates.TemplateResponse("pages/s6_hub.html", {
        "request": request,
        "user": user,
        "can_edit": _can_edit(user, member),
        "plan_status": plan_status,
        "plan_draft": bool(plan and plan.draft_content),
        "channel_count": channel_count,
        "net_count": net_count,
        "radio_total": radio_total,
        "radio_out": radio_out,
        "roster_count": len(roster),
        "shackle_label": f"{shackle.word.upper()}{shackle.offset}" if shackle else "",
    })


@router.get("/emcomm", response_class=HTMLResponse)
@require_auth
async def s6_emcomm_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_view(user, member):
        raise HTTPException(status_code=403, detail="Restricted")
    plan = (await db.execute(select(S6EmcommPlan).where(S6EmcommPlan.id == 1))).scalar_one_or_none()
    return templates.TemplateResponse("pages/s6_emcomm.html", {
        "request": request,
        "user": user,
        "can_edit": _can_edit(user, member),
        "plan": plan,
    })


@router.get("/channels", response_class=HTMLResponse)
@require_auth
async def s6_channels_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_view(user, member):
        raise HTTPException(status_code=403, detail="Restricted")
    channels = (await db.execute(select(S6Channel).order_by(S6Channel.ordinal, S6Channel.id))).scalars().all()
    return templates.TemplateResponse("pages/s6_channels.html", {
        "request": request,
        "user": user,
        "can_edit": _can_edit(user, member),
        "channels": channels,
    })


@router.get("/nets", response_class=HTMLResponse)
@require_auth
async def s6_nets_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_view(user, member):
        raise HTTPException(status_code=403, detail="Restricted")
    nets = (await db.execute(select(S6NetSchedule).order_by(S6NetSchedule.id))).scalars().all()
    return templates.TemplateResponse("pages/s6_nets.html", {
        "request": request,
        "user": user,
        "can_edit": _can_edit(user, member),
        "nets": nets,
    })


@router.get("/radios", response_class=HTMLResponse)
@require_auth
async def s6_radios_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_view(user, member):
        raise HTTPException(status_code=403, detail="Restricted")
    radios = (await db.execute(select(S6Radio).order_by(S6Radio.name))).scalars().all()
    return templates.TemplateResponse("pages/s6_radios.html", {
        "request": request,
        "user": user,
        "can_edit": _can_edit(user, member),
        "radios": radios,
    })


@router.get("/roster", response_class=HTMLResponse)
@require_auth
async def s6_roster_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_view(user, member):
        raise HTTPException(status_code=403, detail="Restricted")
    roster = await _comms_roster(db)
    return templates.TemplateResponse("pages/s6_roster.html", {
        "request": request,
        "user": user,
        "roster": roster,
        "rank_abbr": abbr_map(),
    })


def _denied() -> HTMLResponse:
    return HTMLResponse('<span style="color:#c62828;">❌ Not authorized</span>')


# ─── EmComm Plan ─────────────────────────────────────────────────────────────

@router.post("/emcomm/save-draft")
@require_auth
async def emcomm_save_draft(request: Request, content: str = Form(""), db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    plan = (await db.execute(select(S6EmcommPlan).where(S6EmcommPlan.id == 1))).scalar_one_or_none()
    if not plan:
        plan = S6EmcommPlan(id=1, content="", draft_content="")
        db.add(plan)
    plan.draft_content = content
    plan.updated_at = now_ct()
    plan.updated_by = user.get("username")
    await db.commit()
    return HTMLResponse('<span style="color:#2e7d32;">✓ Draft saved</span>')


@router.post("/emcomm/publish")
@require_auth
async def emcomm_publish(request: Request, content: str = Form(""), db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    plan = (await db.execute(select(S6EmcommPlan).where(S6EmcommPlan.id == 1))).scalar_one_or_none()
    if not plan:
        plan = S6EmcommPlan(id=1, content="", draft_content="")
        db.add(plan)
    if content:
        plan.draft_content = content
    if plan.draft_content is not None:
        plan.content = plan.draft_content
        plan.draft_content = None
    plan.published_at = now_ct()
    plan.updated_at = now_ct()
    plan.updated_by = user.get("username")
    await db.commit()
    return HTMLResponse('<span style="color:#2e7d32;">✓ EmComm Plan published</span>')


@router.post("/emcomm/discard")
@require_auth
async def emcomm_discard(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    plan = (await db.execute(select(S6EmcommPlan).where(S6EmcommPlan.id == 1))).scalar_one_or_none()
    if plan:
        plan.draft_content = None
        plan.updated_at = now_ct()
        await db.commit()
    return HTMLResponse('<span style="color:#888;">✓ Draft discarded</span>')


@router.get("/emcomm/pdf")
@require_auth
async def emcomm_pdf(request: Request, db: AsyncSession = Depends(get_db)):
    """Export the published EmComm Plan as a branded PDF."""
    from fpdf import FPDF

    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_view(user, member):
        raise HTTPException(status_code=403, detail="Restricted")
    plan = (await db.execute(select(S6EmcommPlan).where(S6EmcommPlan.id == 1))).scalar_one_or_none()

    def _safe(text):
        if not text:
            return ""
        return text.encode("latin-1", "replace").decode("latin-1")

    def _html_to_text(src):
        if not src:
            return ""
        s = _html.unescape(src)
        s = re.sub(r"(?i)<br\s*/?>", "\n", s)
        s = re.sub(r"</p>|</div>|</li>|</h[1-6]>", "\n", s)
        s = re.sub(r"<li[^>]*>", "\u2022 ", s)
        s = re.sub(r"<[^>]+>", "", s)
        s = _html.unescape(s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Header bar
    pdf.set_fill_color(10, 12, 24)
    pdf.rect(0, 0, 210, 22, 'F')
    pdf.set_xy(10, 6)
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_text_color(212, 165, 55)
    pdf.cell(0, 10, _safe('13th Legion — Emergency Comms Plan'))
    pdf.set_xy(10, 16)
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(180, 180, 180)
    pdf.cell(0, 6, _safe('S6 — Communications | DFW Area of Operations'))

    pdf.set_y(28)
    pdf.set_text_color(30, 30, 30)
    pdf.set_font('Helvetica', '', 10)
    body = _html_to_text(plan.content if plan else "")
    if not body:
        body = "(No published EmComm Plan.)"
    for line in body.split("\n"):
        pdf.multi_cell(0, 5.5, _safe(line))

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/pdf",
        headers={'Content-Disposition': 'attachment; filename="EmComm_Plan_13th_Legion.pdf"'},
    )


# ─── Channels ────────────────────────────────────────────────────────────────

@router.post("/channel")
@require_auth
async def channel_add(
    request: Request,
    channel_no: str = Form(""), channel_type: str = Form(""), alias: str = Form(""),
    rx_freq: str = Form(""), tx_freq: str = Form(""), tx_power: str = Form(""),
    dec_tone: str = Form(""), enc_tone: str = Form(""), notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    max_ord = (await db.execute(select(S6Channel.ordinal).order_by(S6Channel.ordinal.desc()).limit(1))).scalar() or 0
    db.add(S6Channel(ordinal=max_ord + 1, channel_no=channel_no, channel_type=channel_type, alias=alias,
                     rx_freq=rx_freq, tx_freq=tx_freq, tx_power=tx_power, dec_tone=dec_tone, enc_tone=enc_tone, notes=notes))
    await db.commit()
    return HTMLResponse('<span style="color:#2e7d32;">✓ Channel added</span>')


@router.post("/channel/{cid}/delete")
@require_auth
async def channel_delete(request: Request, cid: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    ch = (await db.execute(select(S6Channel).where(S6Channel.id == cid))).scalar_one_or_none()
    if ch:
        await db.delete(ch)
        await db.commit()
    return HTMLResponse('<span style="color:#888;">✓ Deleted</span>')


# ─── Net schedule ─────────────────────────────────────────────────────────────

@router.post("/net")
@require_auth
async def net_add(
    request: Request,
    day_of_week: str = Form(""), start_time: str = Form(""), frequency: str = Form(""),
    alias: str = Form(""), purpose: str = Form(""), notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    db.add(S6NetSchedule(day_of_week=day_of_week, start_time=start_time, frequency=frequency, alias=alias, purpose=purpose, notes=notes))
    await db.commit()
    return HTMLResponse('<span style="color:#2e7d32;">✓ Net added</span>')


@router.post("/net/{nid}/delete")
@require_auth
async def net_delete(request: Request, nid: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    net = (await db.execute(select(S6NetSchedule).where(S6NetSchedule.id == nid))).scalar_one_or_none()
    if net:
        await db.delete(net)
        await db.commit()
    return HTMLResponse('<span style="color:#888;">✓ Deleted</span>')


# ─── Radio inventory ──────────────────────────────────────────────────────────

@router.post("/radio")
@require_auth
async def radio_add(
    request: Request,
    name: str = Form(""), radio_type: str = Form(""), serial: str = Form(""), notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    db.add(S6Radio(name=name, radio_type=radio_type, serial=serial, status="available", notes=notes))
    await db.commit()
    return HTMLResponse('<span style="color:#2e7d32;">✓ Radio added</span>')


@router.post("/radio/{rid}/checkout")
@require_auth
async def radio_checkout(request: Request, rid: int, checked_out_to: str = Form(""), db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    r = (await db.execute(select(S6Radio).where(S6Radio.id == rid))).scalar_one_or_none()
    if r:
        r.status = "checked_out"
        r.checked_out_to = checked_out_to
        r.checked_out_at = now_ct()
        await db.commit()
    return HTMLResponse('<span style="color:#2e7d32;">✓ Checked out</span>')


@router.post("/radio/{rid}/checkin")
@require_auth
async def radio_checkin(request: Request, rid: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    r = (await db.execute(select(S6Radio).where(S6Radio.id == rid))).scalar_one_or_none()
    if r:
        r.status = "available"
        r.checked_out_to = None
        r.checked_out_at = None
        await db.commit()
    return HTMLResponse('<span style="color:#2e7d32;">✓ Checked in</span>')


@router.post("/radio/{rid}/delete")
@require_auth
async def radio_delete(request: Request, rid: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_edit(user, member):
        return _denied()
    r = (await db.execute(select(S6Radio).where(S6Radio.id == rid))).scalar_one_or_none()
    if r:
        await db.delete(r)
        await db.commit()
    return HTMLResponse('<span style="color:#888;">✓ Deleted</span>')
