"""Weapons Qualification — S3 standalone page to record per-FTX pass/fail."""

import logging
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc

from app.auth import require_auth, get_current_user
from app.database import async_session
from app.models.member import Member
from app.models.events import Event, EventRSVP
from app.constants import RANK_ABBR
from app.models.weapons_qual import MemberWeaponsQual

log = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/training/weapons-qual", tags=["weapons-qual"])

# Same RBAC as Battle Library management: Command + S1 Lead + S3 shop (+ admin)
MANAGE_ROLES = {"command", "s1_lead", "s3", "admin"}
FTX_CATEGORIES = ("ftx", "mcftx")


def _can_manage(user) -> bool:
    return bool(user and set(user.get("roles", [])) & MANAGE_ROLES)


@router.get("", response_class=HTMLResponse)
@require_auth
async def weapons_qual_page(request: Request, event_id: int | None = None):
    """Record weapons qualification results for an FTX. S3/Command/S1-Lead only."""
    user = get_current_user(request)
    if not _can_manage(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    async with async_session() as db:
        # FTX/MCFTX events, most recent first
        events = (await db.execute(
            select(Event)
            .where(Event.category.in_(FTX_CATEGORIES))
            .order_by(desc(Event.date_start))
        )).scalars().all()

        selected = None
        members = []
        if event_id:
            selected = (await db.execute(
                select(Event).where(Event.id == event_id)
            )).scalar_one_or_none()
            if selected:
                # Checked-in attendees for this event
                rows = (await db.execute(
                    select(Member, EventRSVP)
                    .join(EventRSVP, EventRSVP.member_id == Member.id)
                    .where(EventRSVP.event_id == event_id, EventRSVP.checked_in.is_(True))
                    .order_by(Member.last_name, Member.first_name)
                )).all()
                # Existing qual records for this event (member_id -> passed)
                existing = {
                    q.member_id: q for q in (await db.execute(
                        select(MemberWeaponsQual).where(MemberWeaponsQual.event_id == event_id)
                    )).scalars().all()
                }
                for m, _rsvp in rows:
                    rec = existing.get(m.id)
                    rnk = RANK_ABBR.get(m.rank_grade, m.rank_grade or "")
                    name = f"{rnk} {m.last_name}, {m.first_name}".strip()
                    if m.callsign:
                        name += f" ({m.callsign})"
                    members.append({
                        "id": m.id,
                        "name": name,
                        "current": ("pass" if rec.passed else "fail") if rec else None,
                    })

    return templates.TemplateResponse("pages/weapons_qual.html", {
        "request": request,
        "user": user,
        "events": events,
        "selected": selected,
        "members": members,
    })


@router.post("/{event_id}/save")
@require_auth
async def weapons_qual_save(request: Request, event_id: int):
    """Save pass/fail results for checked-in members of an FTX."""
    user = get_current_user(request)
    if not _can_manage(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    form = await request.form()
    recorder = (user or {}).get("username") or "unknown"

    async with async_session() as db:
        event = (await db.execute(
            select(Event).where(Event.id == event_id)
        )).scalar_one_or_none()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        qual_date = event.date_start.date() if event.date_start else None

        # Existing records for this event, keyed by member
        existing = {
            q.member_id: q for q in (await db.execute(
                select(MemberWeaponsQual).where(MemberWeaponsQual.event_id == event_id)
            )).scalars().all()
        }

        # Form fields look like result_<member_id> = pass | fail | none
        for key, val in form.items():
            if not key.startswith("result_"):
                continue
            try:
                mid = int(key[len("result_"):])
            except ValueError:
                continue
            val = (val or "").strip().lower()
            rec = existing.get(mid)
            if val == "none":
                # Clear any existing record for this member+event
                if rec:
                    await db.delete(rec)
                continue
            if val not in ("pass", "fail"):
                continue
            passed = (val == "pass")
            if rec:
                rec.passed = passed
                rec.qualified_on = qual_date
                rec.recorded_by = recorder
                rec.recorded_at = datetime.utcnow()
            else:
                db.add(MemberWeaponsQual(
                    member_id=mid,
                    event_id=event_id,
                    passed=passed,
                    qualified_on=qual_date,
                    recorded_by=recorder,
                ))
        await db.commit()
    log.info(f"Weapons qual saved for event {event_id} by {recorder}")
    return RedirectResponse(url=f"/training/weapons-qual?event_id={event_id}", status_code=303)
