"""Member profile pages — PP-022."""

from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member
from app.models.training import TradocItem, MemberTradoc, Certification, MemberCertification
from app.models.awards import MemberAward
from app.models.rank_history import RankHistory
from app.models.events import Event, EventRSVP

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

from app.constants import RANK_ABBR, RANK_TITLE


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
    # All TRADOC items
    result = await db.execute(select(TradocItem).order_by(TradocItem.sort_order))
    all_items = result.scalars().all()

    # Member's completed items
    result = await db.execute(
        select(MemberTradoc).where(MemberTradoc.member_id == member_id)
    )
    completed = {mt.item_id: mt for mt in result.scalars().all()}

    # Group by block
    blocks = {}
    for item in all_items:
        if item.block not in blocks:
            blocks[item.block] = {"name": item.block_name, "items": []}
        blocks[item.block]["items"].append({
            "id": item.id,
            "name": item.name,
            "done": item.id in completed,
            "signoff": completed.get(item.id),
        })

    total = len(all_items)
    done = len(completed)

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

    return {
        "blocks": blocks,
        "total": total,
        "done": done,
        "pct": round(done / total * 100) if total > 0 else 0,
        "certs": certs,
        "awards": awards,
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
            "rank_abbr": RANK_ABBR,
            "rank_title": RANK_TITLE,
            "is_own": True,
            "can_see_pii": True,
            "training": None,
            "rank_history": [],
            "now": datetime.utcnow(),
        })

    training = await _get_training_data(member.id, db)
    rank_history = await _get_rank_history(member.id, db)
    ftx_history = await _get_ftx_history(member.id, db)

    # Fetch NC last login for this member
    nc_last_login = None
    if member.nc_username:
        try:
            login_ms = await _fetch_single_nc_login(member.nc_username)
            if login_ms and login_ms > 0:
                from zoneinfo import ZoneInfo; nc_last_login = datetime.fromtimestamp(login_ms / 1000, tz=ZoneInfo("America/Chicago"))
        except Exception:
            pass

    return templates.TemplateResponse("pages/profile.html", {
        "request": request,
        "user": user,
        "member": member,
        "rank_abbr": RANK_ABBR,
        "rank_title": RANK_TITLE,
        "is_own": True,
        "can_see_pii": True,
        "training": training,
        "rank_history": rank_history,
        "ftx_history": ftx_history,
        "nc_last_login": nc_last_login,
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

    # Fetch NC last login for this member
    nc_last_login = None
    if member.nc_username:
        try:
            login_ms = await _fetch_single_nc_login(member.nc_username)
            if login_ms and login_ms > 0:
                from zoneinfo import ZoneInfo; nc_last_login = datetime.fromtimestamp(login_ms / 1000, tz=ZoneInfo("America/Chicago"))
        except Exception:
            pass

    return templates.TemplateResponse("pages/profile.html", {
        "request": request,
        "user": user,
        "member": member,
        "rank_abbr": RANK_ABBR,
        "rank_title": RANK_TITLE,
        "is_own": is_own,
        "can_see_pii": can_see_pii,
        "training": training,
        "rank_history": rank_history,
        "ftx_history": ftx_history,
        "nc_last_login": nc_last_login,
        "now": datetime.utcnow(),
    })
