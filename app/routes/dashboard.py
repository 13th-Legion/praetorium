"""Dashboard route — authenticated landing page."""

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_

from app.auth import require_auth
from app.database import async_session
from app.models.elections import Election, ElectionBallot
from app.models.member import Member
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

    return templates.TemplateResponse("pages/dashboard.html", {
        "request": request,
        "user": user,
        "is_command": bool(roles & COMMAND_ROLES),
        "is_s1_lead": "s1_lead" in roles or bool(roles & {"command", "admin"}),
        "active_election": active_election,
        "election_winner_name": election_winner_name,
    })
