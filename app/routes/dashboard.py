"""Dashboard route — authenticated landing page."""
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_

from app.auth import require_auth
from app.database import async_session
from app.models.elections import Election, ElectionBallot
from app.models.member import Member
from app.models.events import Event
from app.models.rank_history import RankHistory
from app.models.ribbons import MemberRibbon, RibbonCatalog
from app.routes.elections import _auto_advance
from app.constants import RANK_ABBR

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


def _member_name(m: Member) -> str:
    """Rank-abbr + last name (+ callsign) for feed rows."""
    abbr = RANK_ABBR.get(m.rank_grade, m.rank_grade or "")
    base = f"{abbr} {m.last_name}".strip()
    return base


@router.get("/api/dashboard/activity-feed", response_class=HTMLResponse)
@require_auth
async def activity_feed(request: Request):
    """Unit-wide Promotions & Awards feed (PP-253).

    Merges rank_history (promotions) + member_ribbons (manual/claim ribbon &
    decoration grants) into one reverse-chronological stream. Auto-derived
    ribbons are NOT rows in member_ribbons, so they correctly never appear here
    (they are not discrete 'events').
    """
    items = []  # each: {dt, kind, icon, member_id, name, callsign, text, img}
    async with async_session() as db:
        # Promotions — skip the very first (initial) assignment where old_rank is null
        pr = await db.execute(
            select(RankHistory, Member)
            .join(Member, Member.id == RankHistory.member_id)
            .where(RankHistory.old_rank.is_not(None))
            .order_by(RankHistory.effective_date.desc())
            .limit(25)
        )
        for rh, m in pr.all():
            old_abbr = RANK_ABBR.get(rh.old_rank, rh.old_rank or "")
            new_abbr = RANK_ABBR.get(rh.new_rank, rh.new_rank or "")
            items.append({
                "dt": rh.effective_date,
                "kind": "promotion",
                "icon": "\U0001F53C",  # up-triangle
                "member_id": m.id,
                "name": _member_name(m),
                "callsign": m.callsign,
                "text": f"promoted {old_abbr} \u2192 {new_abbr}",
                "img": None,
            })

        # Ribbon / decoration grants (manual + claim only — auto isn't stored)
        rr = await db.execute(
            select(MemberRibbon, Member, RibbonCatalog)
            .join(Member, Member.id == MemberRibbon.member_id)
            .join(RibbonCatalog, RibbonCatalog.code == MemberRibbon.ribbon_code)
            .order_by(MemberRibbon.awarded_at.desc())
            .limit(25)
        )
        for mr, m, cat in rr.all():
            verb = "awarded"
            items.append({
                "dt": mr.awarded_at,
                "kind": "award",
                "icon": "\U0001F396\uFE0F",  # medal
                "member_id": m.id,
                "name": _member_name(m),
                "callsign": m.callsign,
                "text": f"{verb} {cat.name}",
                "img": cat.image,
            })

    # Merge, newest first, cap the stream
    items = [it for it in items if it["dt"] is not None]
    items.sort(key=lambda it: it["dt"], reverse=True)
    items = items[:12]

    if not items:
        return HTMLResponse(
            '<div style="padding:14px 0;color:#888;font-size:13px;text-align:center;">'
            'No recent promotions or awards.</div>'
        )

    def _row(it):
        d = _to_cdt(it["dt"])
        datestr = d.strftime("%d %b").upper().lstrip("0") if d else ""
        cs = f' <span style="color:#888;font-style:italic;">\u201c{it["callsign"]}\u201d</span>' if it["callsign"] else ""
        thumb = (
            f'<img src="/static/img/ribbons/{it["img"]}" alt="" '
            f'style="width:22px;height:22px;object-fit:contain;flex-shrink:0;" '
            f'onerror="this.style.display=\'none\'">'
            if it.get("img") else
            f'<span style="font-size:16px;width:22px;text-align:center;flex-shrink:0;">{it["icon"]}</span>'
        )
        return (
            f'<a href="/profile/{it["member_id"]}" '
            f'style="display:flex;align-items:center;gap:10px;padding:9px 4px;'
            f'border-bottom:1px solid rgba(255,255,255,0.06);text-decoration:none;color:inherit;">'
            f'{thumb}'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="font-size:13px;line-height:1.3;">'
            f'<span style="font-weight:600;color:#d4a537;">{it["name"]}</span>{cs} '
            f'<span style="color:#ccc;">{it["text"]}</span></div>'
            f'</div>'
            f'<span style="font-size:11px;color:#777;white-space:nowrap;flex-shrink:0;">{datestr}</span>'
            f'</a>'
        )

    rows = "".join(_row(it) for it in items)
    # Duplicate the rows once so the vertical marquee loops seamlessly.
    # CSS (portal.css .feed-*) handles the auto-scroll + pause-on-hover.
    scroll_body = (
        f'<div class="feed-scroll"><div class="feed-track">{rows}{rows}</div></div>'
        if len(items) > 4 else
        f'<div>{rows}</div>'
    )
    return HTMLResponse(scroll_body)
