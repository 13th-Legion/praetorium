"""PP-043: FTX Attendance Analytics & Reporting."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_, case, desc

from app.auth import require_auth, get_current_user
from app.database import async_session
from app.models.events import Event, EventRSVP
from app.models.member import Member

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

RANK_ABBR = {
    "e1": "PV2", "e2": "PV2", "e3": "PFC", "e4": "SPC", "e5": "SGT",
    "e6": "SSG", "e7": "SFC", "e8": "1SG", "e9": "SGM",
    "o1": "2LT", "o2": "1LT", "o3": "CPT", "o4": "MAJ",
    "w1": "WO1", "w2": "CW2", "w3": "CW3",
}

TEAM_LABELS = {
    "alpha": "Alpha", "bravo": "Bravo", "charlie": "Charlie",
    "delta": "Delta", "echo": "Echo", "foxtrot": "Foxtrot",
}


def _has_access(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & {"command", "s3", "s1", "admin", "leader"})


@router.get("/api/s3/attendance-analytics", response_class=HTMLResponse)
@require_auth
async def attendance_analytics(request: Request):
    """FTX Attendance Analytics dashboard."""
    user = get_current_user(request)
    if not _has_access(user):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    async with async_session() as db:
        # Get all finalized FTX/MCFTX events
        events_result = await db.execute(
            select(Event).where(
                Event.category.in_(["ftx", "mcftx"]),
                Event.finalized_at.isnot(None),
            ).order_by(desc(Event.date_start))
        )
        events = events_result.scalars().all()

        if not events:
            return templates.TemplateResponse("pages/attendance_analytics.html", {
                "request": request,
                "user": user,
                "events": [],
                "member_stats": [],
                "team_stats": [],
                "event_stats": [],
                "total_events": 0,
                "avg_attendance": 0,
                "avg_rate": 0,
                "no_shows": [],
            })

        event_ids = [e.id for e in events]
        total_events = len(events)

        # Get all active/recruit members
        members_result = await db.execute(
            select(Member).where(Member.status.in_(["active", "recruit"]))
        )
        all_members = members_result.scalars().all()
        roster_strength = len(all_members)

        # Get all RSVPs for finalized events
        rsvps_result = await db.execute(
            select(EventRSVP).where(EventRSVP.event_id.in_(event_ids))
        )
        all_rsvps = rsvps_result.scalars().all()

        # === Per-Event Stats ===
        event_stats = []
        for evt in events:
            evt_rsvps = [r for r in all_rsvps if r.event_id == evt.id]
            attended = len([r for r in evt_rsvps if r.attended])
            rsvp_attending = len([r for r in evt_rsvps if r.status == "attending"])
            declined = len([r for r in evt_rsvps if r.status == "declined"])
            no_show = rsvp_attending - attended if rsvp_attending > attended else 0

            from app.routes.events import _to_cdt
            local_dt = _to_cdt(evt.date_start)

            event_stats.append({
                "id": evt.id,
                "title": evt.title,
                "date": local_dt.strftime("%d %b %Y").lstrip("0"),
                "date_short": local_dt.strftime("%b %y"),
                "category": evt.category.upper(),
                "attended": attended,
                "rsvp_attending": rsvp_attending,
                "declined": declined,
                "roster_strength": roster_strength,
                "rate": round(attended / roster_strength * 100) if roster_strength else 0,
                "no_show": no_show,
            })

        avg_attendance = round(sum(e["attended"] for e in event_stats) / total_events, 1) if total_events else 0
        avg_rate = round(sum(e["rate"] for e in event_stats) / total_events) if total_events else 0

        # === Per-Member Stats ===
        member_stats = []
        for m in all_members:
            m_rsvps = [r for r in all_rsvps if r.member_id == m.id]
            attended_count = len([r for r in m_rsvps if r.attended])
            rsvp_yes_count = len([r for r in m_rsvps if r.status == "attending"])
            no_show_count = rsvp_yes_count - attended_count if rsvp_yes_count > attended_count else 0

            # Attendance rate = attended / total finalized events
            rate = round(attended_count / total_events * 100) if total_events else 0

            # Last attended
            attended_event_ids = [r.event_id for r in m_rsvps if r.attended]
            last_attended = None
            if attended_event_ids:
                last_evt = next((e for e in events if e.id in attended_event_ids), None)
                if last_evt:
                    from app.routes.events import _to_cdt
                    last_attended = _to_cdt(last_evt.date_start).strftime("%b %Y")

            rank = RANK_ABBR.get(m.rank_grade, "")
            member_stats.append({
                "id": m.id,
                "name": f"{rank} {m.last_name}" if rank else m.last_name,
                "full_name": f"{m.first_name} {m.last_name}",
                "callsign": m.callsign or "",
                "team": TEAM_LABELS.get(m.team, m.team or "Unassigned"),
                "attended": attended_count,
                "total": total_events,
                "rate": rate,
                "no_shows": no_show_count,
                "last_attended": last_attended or "Never",
                "status": m.status,
            })

        member_stats.sort(key=lambda x: x["rate"], reverse=True)

        # === Per-Team Stats ===
        team_stats = []
        for team_key, team_label in TEAM_LABELS.items():
            team_members = [m for m in all_members if m.team == team_key]
            if not team_members:
                continue
            team_member_ids = {m.id for m in team_members}
            team_attended_total = 0
            for evt in events:
                evt_rsvps = [r for r in all_rsvps if r.event_id == evt.id and r.member_id in team_member_ids]
                team_attended_total += len([r for r in evt_rsvps if r.attended])

            possible = len(team_members) * total_events
            rate = round(team_attended_total / possible * 100) if possible else 0

            team_stats.append({
                "team": team_label,
                "members": len(team_members),
                "total_attended": team_attended_total,
                "possible": possible,
                "rate": rate,
            })

        team_stats.sort(key=lambda x: x["rate"], reverse=True)

        # === No-Show Report ===
        no_shows = [m for m in member_stats if m["no_shows"] > 0]
        no_shows.sort(key=lambda x: x["no_shows"], reverse=True)

    return templates.TemplateResponse("pages/attendance_analytics.html", {
        "request": request,
        "user": user,
        "events": events,
        "member_stats": member_stats,
        "team_stats": team_stats,
        "event_stats": event_stats,
        "total_events": total_events,
        "avg_attendance": avg_attendance,
        "avg_rate": avg_rate,
        "roster_strength": roster_strength,
        "no_shows": no_shows,
    })
