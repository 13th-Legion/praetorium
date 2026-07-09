"""Dashboard route — authenticated landing page."""
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_

from app.auth import require_auth
from app.database import async_session
from app.models.elections import Election, ElectionBallot
from app.models.member import Member
from app.models.events import Event
from app.routes.elections import _auto_advance

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")

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

def _mildate(dt):
    if dt is None:
        return ""
    local = _to_cdt(dt)
    tz_label = local.strftime("%Z")
    return local.strftime("%d %b %Y").upper().lstrip("0") + f" @ {local.strftime('%H%M')} {tz_label}"
templates.env.filters["mildate"] = _mildate

from app.constants import COMMAND_ROLES


@router.get("/dashboard")
@require_auth
async def dashboard(request: Request):
    user = request.session.get("user", {})
    roles = set(user.get("roles", []))

    # Check for an active election to show banner
    active_election = None
    election_winner_name = None
    async with async_session() as db:
        result = await db.execute(
            select(Election)
            .where(Election.phase.in_(["scheduled", "nominations", "voting", "complete"]))
            .order_by(Election.created_at.desc())
            .limit(1)
        )
        active_election = result.scalar_one_or_none()

        # Auto-advance phase based on schedule
        if active_election:
            await _auto_advance(db, active_election)

        # Auto-archive: hide "complete" elections after 14 days
        if active_election and active_election.phase == "complete":
            if active_election.voting_close:
                days_since = (datetime.utcnow() - active_election.voting_close).days
                if days_since > 14:
                    active_election.phase = "archived"
                    await db.commit()
                    active_election = None  # hide the banner

        # If complete, fetch winner name from ballot counts
        if active_election and active_election.phase == "complete":
            ballot_result = await db.execute(
                select(ElectionBallot.nominee_id, func.count(ElectionBallot.id))
                .where(ElectionBallot.election_id == active_election.id)
                .group_by(ElectionBallot.nominee_id)
                .order_by(func.count(ElectionBallot.id).desc())
                .limit(1)
            )
            top = ballot_result.first()
            if top:
                winner_result = await db.execute(
                    select(Member).where(Member.id == top[0])
                )
                winner = winner_result.scalar_one_or_none()
                if winner:
                    election_winner_name = winner.display_name

    # Latest published AARs for dashboard card (PP-245).
    # Order by EVENT date_start desc (not publish time) so a late-published AAR
    # for an old event does not jump ahead of newer events' AARs.
    latest_aars = []
    async with async_session() as db2:
        _r = await db2.execute(
            select(Event)
            .where(Event.aar_published_at.is_not(None))
            .order_by(Event.date_start.desc())
            .limit(3)
        )
        for _ev in _r.scalars().all():
            _intent = (_ev.aar_commander_intent or "").strip()
            latest_aars.append({
                "id": _ev.id,
                "title": _ev.title,
                "date_start": _ev.date_start,
                "published_at": _ev.aar_published_at,
                "snippet": _intent[:120] + ("\u2026" if len(_intent) > 120 else ""),
            })

    return templates.TemplateResponse("pages/dashboard.html", {
        "request": request,
        "user": user,
        "is_command": bool(roles & COMMAND_ROLES),
        "is_s1_lead": "s1_lead" in roles or bool(roles & {"command", "admin"}),
        "active_election": active_election,
        "election_winner_name": election_winner_name,
        "latest_aars": latest_aars,
    })
