"""Profile summary API — dashboard 'Your Status' card."""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member
from app.models.training import TradocItem, MemberTradoc, Certification, MemberCertification
from app.models.awards import MemberAward

router = APIRouter(prefix="/api/profile", tags=["profile_summary"])

from app.constants import RANK_ABBR


def _time_in_service(join_date: Optional[date]) -> str:
    """Calculate human-friendly time in service."""
    if not join_date:
        return "—"
    today = date.today()
    months = (today.year - join_date.year) * 12 + (today.month - join_date.month)
    years = months // 12
    rem = months % 12
    if years > 0 and rem > 0:
        return f"{years}y {rem}m"
    elif years > 0:
        return f"{years}y"
    else:
        return f"{rem}m"


@router.get("/summary", response_class=HTMLResponse)
@require_auth
async def profile_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the dashboard 'Your Status' card as an HTMX partial."""
    user = get_current_user(request)
    username = user["username"]

    # Look up member
    result = await db.execute(
        select(Member).where(Member.nc_username == username)
    )
    member = result.scalar_one_or_none()

    if not member:
        return HTMLResponse(f"""
        <div style="text-align:center;padding:20px;">
            <div style="font-size:16px;font-weight:600;margin-bottom:4px;">{user.get("display_name", username)}</div>
            <p class="text-muted">No personnel record found.<br>Contact S1 if this is an error.</p>
        </div>""")

    rank_abbr = RANK_ABBR.get(member.rank_grade, member.rank_grade or "—")
    rank_display = f"{rank_abbr} {member.last_name}"
    callsign_display = f'"{member.callsign}"' if member.callsign else ""
    tis = _time_in_service(member.join_date)

    # Team + billet
    assignment_parts = []
    if member.team:
        assignment_parts.append(member.team)
    if member.primary_billet:
        assignment_parts.append(member.primary_billet)
    assignment = " · ".join(assignment_parts) if assignment_parts else "Unassigned"

    # Status badge
    status = (member.status or "unknown").capitalize()
    if status == "Active":
        status_color = "#2e7d32"
    elif status == "Recruit":
        status_color = "#ef6c00"
    else:
        status_color = "#888"

    # FTX info
    ftx_str = str(member.ftx_count or 0)
    last_ftx = member.last_ftx.strftime("%b %Y") if member.last_ftx else "Never"

    # TRADOC progress
    total_result = await db.execute(select(func.count(TradocItem.id)))
    total_items = total_result.scalar() or 0

    completed_result = await db.execute(
        select(func.count(MemberTradoc.id))
        .where(MemberTradoc.member_id == member.id)
    )
    completed_items = completed_result.scalar() or 0

    tradoc_pct = round(completed_items / total_items * 100) if total_items > 0 else 0

    if member.status == "active":
        tradoc_html = '<span style="color:#2e7d32;font-weight:600;">✅ Patched</span>'
    else:
        bar_color = "#2e7d32" if tradoc_pct >= 75 else "#ef6c00" if tradoc_pct >= 40 else "#c62828"
        tradoc_html = f"""
        <div style="display:flex;align-items:center;gap:8px;">
            <div style="flex:1;height:8px;background:rgba(255,255,255,0.1);border-radius:4px;overflow:hidden;">
                <div style="width:{tradoc_pct}%;height:100%;background:{bar_color};border-radius:4px;"></div>
            </div>
            <span style="font-size:12px;color:#aaa;">{completed_items}/{total_items}</span>
        </div>"""

    # Awards count
    awards_result = await db.execute(
        select(func.count(MemberAward.id))
        .where(MemberAward.member_id == member.id)
    )
    awards_count = awards_result.scalar() or 0
    awards_html = ""
    if awards_count > 0:
        awards_html = f"""
        <div style="display:flex;justify-content:space-between;padding:4px 0;">
            <span style="color:#aaa;">Awards</span>
            <span style="font-weight:600;">🗡️ ×{awards_count}</span>
        </div>"""

    # Certs count
    earned_result = await db.execute(
        select(func.count(MemberCertification.id))
        .where(MemberCertification.member_id == member.id)
    )
    earned_count = earned_result.scalar() or 0
    certs_html = ""
    if earned_count > 0:
        certs_html = f"""
        <div style="display:flex;justify-content:space-between;padding:4px 0;">
            <span style="color:#aaa;">Certifications</span>
            <span style="font-weight:600;">{earned_count} earned</span>
        </div>"""

    # Build the card
    html = f"""
    <div style="text-align:center;margin-bottom:12px;">
        <div style="font-size:20px;font-weight:700;">{rank_display}</div>
        {"<div style='font-size:14px;color:#d4a537;font-style:italic;'>" + callsign_display + "</div>" if callsign_display else ""}
        <div style="margin-top:6px;">
            <span style="background:{status_color};color:#fff;font-size:11px;padding:2px 10px;border-radius:10px;font-weight:600;">{status}</span>
        </div>
    </div>

    <div style="border-top:1px solid rgba(255,255,255,0.08);padding-top:10px;font-size:13px;">
        <div style="display:flex;justify-content:space-between;padding:4px 0;">
            <span style="color:#aaa;">Assignment</span>
            <span style="font-weight:600;text-align:right;">{assignment}</span>
        </div>
        <div style="display:flex;justify-content:space-between;padding:4px 0;">
            <span style="color:#aaa;">Serial</span>
            <span style="font-weight:600;font-family:monospace;">{member.serial_number or "—"}</span>
        </div>
        <div style="display:flex;justify-content:space-between;padding:4px 0;">
            <span style="color:#aaa;">Time in Service</span>
            <span style="font-weight:600;">{tis}</span>
        </div>
        <div style="display:flex;justify-content:space-between;padding:4px 0;">
            <span style="color:#aaa;">FTX Attendance</span>
            <span style="font-weight:600;">{ftx_str} <span style="color:#888;font-weight:400;">· last {last_ftx}</span></span>
        </div>
        {awards_html}
        {certs_html}
        <div style="padding:8px 0 4px;">
            <span style="color:#aaa;font-size:12px;">TRADOC</span>
            {tradoc_html}
        </div>
    </div>

    <div style="margin-top:12px;text-align:center;">
        <a href="/profile" style="color:#d4a537;font-size:13px;text-decoration:none;font-weight:600;">View Full Profile →</a>
    </div>"""

    return HTMLResponse(html)
