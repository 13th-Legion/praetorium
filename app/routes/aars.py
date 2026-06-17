"""AAR Library (PP-XXX) — unit-wide discovery of published After-Action Reviews.

Read-only surfacing of existing AAR data (PP-125). No schema change.
Published AARs are visible to all authenticated members.
"""
import logging
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth import require_auth
from app.database import async_session
from app.models.events import Event

log = logging.getLogger(__name__)

router = APIRouter(tags=["aars"])
templates = Jinja2Templates(directory="app/templates")

_CDT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


def _to_cdt(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_CDT)


def _mildate(dt):
    if dt is None:
        return ""
    local = _to_cdt(dt)
    return local.strftime("%d %b %Y").upper().lstrip("0")


templates.env.filters.setdefault("cdt", _to_cdt)
templates.env.filters.setdefault("mildate_aar", _mildate)


def _summarize(event):
    """Build a lightweight view dict for one published AAR."""
    items = event.aar_items or []
    n_right = sum(1 for i in items if i.category == "right")
    n_wrong = sum(1 for i in items if i.category == "wrong")
    n_improve = sum(1 for i in items if i.category == "improve")
    intent = (event.aar_commander_intent or "").strip()
    snippet = intent[:160] + ("…" if len(intent) > 160 else "")
    return {
        "id": event.id,
        "title": event.title,
        "category": event.category,
        "date_start": event.date_start,
        "published_at": event.aar_published_at,
        "published_by": event.aar_published_by,
        "snippet": snippet,
        "n_right": n_right,
        "n_wrong": n_wrong,
        "n_improve": n_improve,
    }


@router.get("/aars")
@require_auth
async def aar_library(request: Request):
    user = request.session.get("user", {})
    async with async_session() as db:
        result = await db.execute(
            select(Event)
            .where(Event.aar_published_at.is_not(None))
            .options(selectinload(Event.aar_items))
            .order_by(Event.aar_published_at.desc())
        )
        events = result.scalars().all()

    aars = [_summarize(e) for e in events]

    # distinct years + types for filters
    years = sorted({_to_cdt(a["published_at"]).year for a in aars if a["published_at"]}, reverse=True)
    types = sorted({a["category"] for a in aars if a["category"]})

    return templates.TemplateResponse("pages/aar_library.html", {
        "request": request,
        "user": user,
        "aars": aars,
        "years": years,
        "types": types,
    })
