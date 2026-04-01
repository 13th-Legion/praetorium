"""Events & RSVP routes — PP-027.

Calendar sync from NC CalDAV, events CRUD, RSVP management,
dashboard widget, and full events pages.
"""

import re
from datetime import datetime, date, timedelta, time as dtime
from typing import Optional

import httpx
from dateutil.rrule import rrulestr
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_, or_, case, update
from sqlalchemy.orm import selectinload

from app.auth import require_auth, require_role, get_current_user
from app.database import async_session
from app.models.events import Event, EventRSVP, EventDocument
from app.models.member import Member
from app.models.training import TradocItem, MemberTradoc
from config import get_settings
from app.constants import RANK_ABBR

router = APIRouter(tags=["events"])
templates = Jinja2Templates(directory="app/templates")

# Register CDT filter for all templates in this module
from zoneinfo import ZoneInfo
_CDT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")

def _to_cdt(dt):
    """Convert naive UTC datetime to CDT."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_CDT)

templates.env.filters["cdt"] = _to_cdt

# ─── CalDAV Config ───────────────────────────────────────────────────────────

CALENDAR_PATH = "/remote.php/dav/calendars/spooky/13th-legion/"
from app.settings import NC_SVC_USER as CALENDAR_USER, NC_SVC_PASS as CALENDAR_PASS

REPORT_BODY = """<?xml version="1.0"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <c:calendar-data/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start}" end="{end}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

# ─── Category & Icon Mapping ────────────────────────────────────────────────

VALID_CATEGORIES = [
    "ftx", "mcftx", "online_training", "meeting", "external_training",
    "family_day", "social", "volunteering", "other",
]

CATEGORY_LABELS = {
    "ftx": "FTX",
    "mcftx": "MCFTX",
    "online_training": "Online Training",
    "meeting": "Meeting",
    "external_training": "External Training",
    "family_day": "Family Day",
    "social": "Social",
    "volunteering": "Volunteering",
    "other": "Other",
}

# Categories that get RSVP by default (in-person / planning-dependent)
RSVP_CATEGORIES = {"ftx", "mcftx", "external_training", "family_day", "social", "volunteering"}

# WARNO auto-schedule lead times (days before event start)
WARNO_LEAD_DAYS = {
    "ftx": 14,    # 2 weeks
    "mcftx": 28,  # 4 weeks
}


def _calc_warno_schedule(category: str, date_start) -> "datetime | None":
    """Calculate WARNO auto-issue date based on category defaults."""
    lead = WARNO_LEAD_DAYS.get(category)
    if lead and date_start:
        from datetime import timedelta
        return date_start - timedelta(days=lead)
    return None

CATEGORY_ICONS = {
    "ftx": "🏕️",
    "mcftx": "⚔️",
    "online_training": "💻",
    "meeting": "🎖️",
    "external_training": "🎓",
    "family_day": "👨‍👩‍👧‍👦",
    "social": "🤝",
    "volunteering": "🫡",
    "other": "📅",
}

# Filter tab groupings
FILTER_TABS = {
    "all": {"label": "All", "categories": None},
    "ftx": {"label": "FTX", "categories": ["ftx", "mcftx"]},
    "training": {"label": "Training", "categories": ["online_training", "external_training"]},
    "meetings": {"label": "Meetings", "categories": ["meeting"]},
    "social": {"label": "Social", "categories": ["social", "family_day", "volunteering"]},
}

# Keyword → category auto-mapping for CalDAV sync
TITLE_CATEGORY_MAP = [
    (["multi-company field training", "mcftx", "multi company"], "mcftx"),
    (["field training exercise", "ftx"], "ftx"),
    (["online training", "virtual training"], "online_training"),
    (["leaders meeting", "command meeting", "s3 shop", "nco meeting", "meeting"], "meeting"),
    (["uscca", "external training", "outside training"], "external_training"),
    (["family day", "team day"], "family_day"),
    (["urban evasion", "social", "secret santa", "bbq", "cookout"], "social"),
    (["volunteering", "community service"], "volunteering"),
]


def _guess_category(title: str) -> str:
    """Auto-map event title to category using keyword matching."""
    lower = title.lower()
    for keywords, category in TITLE_CATEGORY_MAP:
        for kw in keywords:
            if kw in lower:
                return category
    return "other"


def _get_icon(category: str) -> str:
    return CATEGORY_ICONS.get(category, "📅")


# ─── iCal Parsing (reused from original) ────────────────────────────────────

def _unescape_ical_text(text: str) -> str:
    """Unescape iCal text property values (RFC 5545 §3.3.11)."""
    if not text:
        return text
    return (text
            .replace("\\n", "\n")
            .replace("\\N", "\n")
            .replace("\\,", ",")
            .replace("\\;", ";")
            .replace("\\\\", "\\"))


def _parse_ical_date(dtstr: str) -> Optional[datetime]:
    dtstr = dtstr.strip()
    if ":" in dtstr:
        dtstr = dtstr.split(":")[-1]
    try:
        if "T" in dtstr:
            if dtstr.endswith("Z"):
                return datetime.strptime(dtstr, "%Y%m%dT%H%M%SZ")
            return datetime.strptime(dtstr, "%Y%m%dT%H%M%S")
        else:
            return datetime.strptime(dtstr, "%Y%m%d")
    except ValueError:
        return None


def _expand_recurring(start: datetime, rrule_str: str,
                      window_start: datetime, window_end: datetime,
                      duration: Optional[timedelta] = None) -> list[tuple[datetime, Optional[datetime]]]:
    try:
        dtstart_str = start.strftime("%Y%m%dT%H%M%S") if start.hour or start.minute else start.strftime("%Y%m%d")
        rule_text = f"DTSTART:{dtstart_str}\nRRULE:{rrule_str}"
        rule = rrulestr(rule_text, ignoretz=True)
        occurrences = []
        for occ in rule.between(window_start, window_end, inc=True):
            end = occ + duration if duration else None
            occurrences.append((occ, end))
            if len(occurrences) >= 20:
                break
        return occurrences
    except Exception:
        return []


def _parse_events_ical(ical_data: str, window_start: datetime, window_end: datetime) -> list[dict]:
    events = []
    blocks = re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', ical_data, re.DOTALL)
    for block in blocks:
        summary_m = re.search(r'SUMMARY:([^\r\n]+)', block)
        dtstart_m = re.search(r'DTSTART[^:]*:?([^\r\n]+)', block)
        dtend_m = re.search(r'DTEND[^:]*:?([^\r\n]+)', block)
        rrule_m = re.search(r'RRULE:([^\r\n]+)', block)
        location_m = re.search(r'LOCATION:([^\r\n]+)', block)
        desc_m = re.search(r'DESCRIPTION:([^\r\n]+)', block)
        if not summary_m or not dtstart_m:
            continue
        summary = summary_m.group(1).strip()
        start = _parse_ical_date(dtstart_m.group(1))
        end = _parse_ical_date(dtend_m.group(1)) if dtend_m else None
        # iCal all-day events: DTEND is exclusive (day after last day).
        # Detect by date-only format (no "T") and subtract 1 day.
        raw_dtstart = dtstart_m.group(1).strip()
        if ":" in raw_dtstart:
            raw_dtstart = raw_dtstart.split(":")[-1]
        if end and "T" not in raw_dtstart and end > start:
            end = end - timedelta(days=1)
        location = _unescape_ical_text(location_m.group(1).strip()) if location_m else None
        description = _unescape_ical_text(desc_m.group(1).strip()) if desc_m else None
        if not start or start.year < 2020:
            continue
        duration = (end - start) if (start and end) else None
        if rrule_m:
            for occ_start, occ_end in _expand_recurring(start, rrule_m.group(1), window_start, window_end, duration):
                if occ_start.year < 2020:
                    continue
                events.append({"summary": summary, "start": occ_start, "end": occ_end,
                               "location": location, "description": description})
        else:
            events.append({"summary": summary, "start": start, "end": end,
                           "location": location, "description": description})
    events.sort(key=lambda e: e["start"])
    return events


# ─── Formatting Helpers ──────────────────────────────────────────────────────

def _parse_mil_datetime(date_str: str, time_str: str = "") -> datetime:
    """Parse a date string + optional HHMM military time into a naive CT datetime.

    Input is treated as CDT/CST (America/Chicago).
    Stored as naive CT to match CalDAV-synced events.
    date_str: '2026-03-15'
    time_str: '0600' or '' (blank = midnight / all-day)
    """
    d = datetime.strptime(date_str, "%Y-%m-%d")
    if time_str and len(time_str) == 4:
        hour = int(time_str[:2])
        minute = int(time_str[2:])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Invalid time: {time_str}")
        d = d.replace(hour=hour, minute=minute)
    return d  # store as naive CT


def _now_ct() -> datetime:
    """Current time as naive CT datetime — matches DB storage convention."""
    return datetime.now(_CDT).replace(tzinfo=None)


def _format_time_mil(dt: datetime) -> str:
    """Format time as military HHMM CT.

    DB stores naive datetimes in America/Chicago (CalDAV synced with TZID).
    """
    # Determine CDT vs CST label based on date
    local = dt.replace(tzinfo=_CDT) if dt.tzinfo is None else dt.astimezone(_CDT)
    tz_label = local.strftime("%Z")  # CDT or CST
    return f"{dt.hour:02d}{dt.minute:02d} {tz_label}"


def _format_date(dt: datetime, all_day: bool = False) -> str:
    # Include year for past/future events not in the current year
    fmt = "%a, %b %d, %Y" if dt.year != _now_ct().year else "%a, %b %d"
    if all_day:
        return dt.strftime(fmt)
    return dt.strftime(fmt) + " · " + _format_time_mil(dt)


def _format_range(start: datetime, end: Optional[datetime], all_day: bool = False) -> str:
    s = _format_date(start, all_day)
    if end and end != start:
        if all_day and (end - start).days > 1:
            s += f" – {_format_date(end - timedelta(days=1), True)}"
        elif not all_day and end.date() == start.date():
            s += f" – {_format_time_mil(end)}"
        elif not all_day:
            s += f" – {_format_date(end, False)}"
    return s


def _is_admin(user: dict) -> bool:
    """Check if user has command/s3/s1 roles."""
    roles = set(user.get("roles", []))
    return bool(roles & {"command", "s3", "s1", "admin"})


# ─── Calendar Sync ───────────────────────────────────────────────────────────

@router.post("/api/events/sync", response_class=HTMLResponse)
@require_role("command", "s3", "admin")
async def sync_calendar(request: Request):
    """Pull events from NC CalDAV and sync to DB."""
    settings = get_settings()
    nc_url = settings.nc_url
    user = request.session.get("user", {})

    now = datetime.utcnow()
    start = now.strftime("%Y%m%dT%H%M%SZ")
    end = (now + timedelta(days=365)).strftime("%Y%m%dT%H%M%SZ")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(
                "REPORT",
                f"{nc_url}{CALENDAR_PATH}",
                content=REPORT_BODY.format(start=start, end=end),
                headers={"Depth": "1", "Content-Type": "application/xml"},
                auth=(CALENDAR_USER, CALENDAR_PASS),
            )
            resp.raise_for_status()

        window_start = now - timedelta(days=7)  # include very recent past
        window_end = now + timedelta(days=365)
        cal_events = _parse_events_ical(resp.text, window_start, window_end)

        if not cal_events:
            return HTMLResponse('<div class="alert alert-warning">No events found in calendar.</div>')

        created = 0
        updated = 0

        async with async_session() as db:
            # Get all active + recruit members for RSVP creation
            members_result = await db.execute(
                select(Member.id).where(
                    Member.status.in_(["active", "recruit", "Active", "Recruit"])
                )
            )
            member_ids = [r[0] for r in members_result.all()]

            for ev in cal_events:
                # Check for existing event by title + date_start (within same day)
                date_start = ev["start"]
                existing = await db.execute(
                    select(Event).where(
                        and_(
                            Event.title == ev["summary"],
                            func.date(Event.date_start) == date_start.date(),
                        )
                    )
                )
                event = existing.scalar_one_or_none()

                if event:
                    # Update existing
                    event.date_end = ev["end"]
                    event.location = ev.get("location") or event.location
                    event.description = ev.get("description") or event.description
                    event.updated_at = datetime.utcnow()
                    updated += 1
                else:
                    # Create new
                    category = _guess_category(ev["summary"])
                    rsvp_on = category in RSVP_CATEGORIES
                    warno_sched = _calc_warno_schedule(category, date_start)
                    event = Event(
                        title=ev["summary"],
                        category=category,
                        description=ev.get("description"),
                        location=ev.get("location"),
                        date_start=date_start,
                        date_end=ev["end"],
                        status="active",
                        rsvp_enabled=rsvp_on,
                        warno_scheduled_at=warno_sched,
                        created_by=user.get("username", "sync"),
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    db.add(event)
                    await db.flush()  # get event.id

                    # Create pending RSVPs only for RSVP-enabled events
                    if rsvp_on:
                        for mid in member_ids:
                            rsvp = EventRSVP(
                                event_id=event.id,
                                member_id=mid,
                                status="pending",
                                created_at=datetime.utcnow(),
                                updated_at=datetime.utcnow(),
                            )
                            db.add(rsvp)
                    created += 1

            await db.commit()

        return HTMLResponse(
            f'<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;margin-top:12px;">'
            f'✅ Sync complete — {created} created, {updated} updated from {len(cal_events)} calendar events.'
            f'</div>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<div style="padding:12px;background:#b71c1c;color:#fff;border-radius:6px;margin-top:12px;">'
            f'❌ Sync failed: {e}</div>'
        )


# ─── Events List Page ────────────────────────────────────────────────────────

@router.get("/events", response_class=HTMLResponse)
@require_auth
async def events_page(request: Request):
    """Full events list page."""
    user = request.session.get("user", {})
    roles = set(user.get("roles", []))
    tab = request.query_params.get("tab", "all")

    async with async_session() as db:
        # Get member_id for current user
        member_result = await db.execute(
            select(Member.id).where(Member.nc_username == user.get("username", ""))
        )
        member_row = member_result.first()
        member_id = member_row[0] if member_row else None

        # Build query
        query = select(Event).options(selectinload(Event.instructor)).order_by(Event.date_start.desc())

        # Apply category filter
        tab_config = FILTER_TABS.get(tab, FILTER_TABS["all"])
        if tab_config["categories"]:
            query = query.where(Event.category.in_(tab_config["categories"]))

        result = await db.execute(query)
        events = result.scalars().all()

        # Batch RSVP counts — 1 query instead of N
        event_ids = [e.id for e in events]
        rsvp_stats_map = {}  # {event_id: {status: count}}
        if event_ids:
            rsvp_stats = await db.execute(
                select(
                    EventRSVP.event_id,
                    EventRSVP.status,
                    func.count(EventRSVP.id)
                ).where(EventRSVP.event_id.in_(event_ids))
                .group_by(EventRSVP.event_id, EventRSVP.status)
            )
            for eid, status, cnt in rsvp_stats.all():
                rsvp_stats_map.setdefault(eid, {})[status] = cnt

        # Batch user RSVPs — 1 query instead of N
        my_rsvps_map = {}  # {event_id: status}
        if member_id and event_ids:
            my_rsvps = await db.execute(
                select(EventRSVP.event_id, EventRSVP.status).where(
                    and_(EventRSVP.member_id == member_id, EventRSVP.event_id.in_(event_ids))
                )
            )
            for eid, status in my_rsvps.all():
                my_rsvps_map[eid] = status

        events_data = []
        now = _now_ct()
        for event in events:
            counts = rsvp_stats_map.get(event.id, {})
            attending = counts.get("attending", 0)
            declined = counts.get("declined", 0)
            pending = counts.get("pending", 0)
            total = attending + declined + pending

            is_past = event.date_start < now
            all_day = (event.date_start.hour == 0 and event.date_start.minute == 0
                       and (not event.date_end or (event.date_end.hour == 0 and event.date_end.minute == 0)))

            events_data.append({
                "event": event,
                "icon": _get_icon(event.category),
                "category_label": CATEGORY_LABELS.get(event.category, event.category),
                "date_display": _format_range(event.date_start, event.date_end, all_day),
                "attending": attending,
                "declined": declined,
                "pending": pending,
                "total": total,
                "my_rsvp": my_rsvps_map.get(event.id),
                "is_past": is_past,
            })
            
        # Get members for the instructor dropdown
        all_members_result = await db.scalars(select(Member).where(Member.status.in_(["active", "recruit"])).order_by(Member.last_name))
        members = all_members_result.all()

    # Split into upcoming and past
    upcoming = [e for e in events_data if not e["is_past"]]
    upcoming.reverse()  # ascending for upcoming
    past = [e for e in events_data if e["is_past"]]  # already desc from query

    # Needs Finalization — past FTX/MCFTX not yet finalized (Command/S3/S1 only)
    needs_finalization = []
    if roles & {"command", "s3", "s1", "admin"}:
        needs_finalization = [
            e for e in past
            if e["event"].category in ("ftx", "mcftx")
            and not e["event"].finalized_at
        ]

    return templates.TemplateResponse("pages/events.html", {
        "request": request,
        "user": user,
        "is_admin": _is_admin(user),
        "upcoming": upcoming,
        "past": past,
        "needs_finalization": needs_finalization,
        "current_tab": tab,
        "filter_tabs": FILTER_TABS,
        "categories": VALID_CATEGORIES,
        "category_labels": CATEGORY_LABELS,
        "members": members,
        "rank_abbr": RANK_ABBR,
    })


# ─── Event Detail Page ───────────────────────────────────────────────────────

@router.get("/events/{event_id}", response_class=HTMLResponse)
@require_auth
async def event_detail(request: Request, event_id: int):
    """Event detail page with RSVP and roster."""
    user = request.session.get("user", {})

    async with async_session() as db:
        result = await db.execute(
            select(Event).options(
                selectinload(Event.documents),
                selectinload(Event.instructor)
            ).where(Event.id == event_id)
        )
        event = result.scalar_one_or_none()
        if not event:
            return HTMLResponse("<h1>Event not found</h1>", status_code=404)

        # Get member_id
        member_result = await db.execute(
            select(Member.id).where(Member.nc_username == user.get("username", ""))
        )
        member_row = member_result.first()
        member_id = member_row[0] if member_row else None

        # My RSVP
        my_rsvp = None
        if member_id:
            rsvp_result = await db.execute(
                select(EventRSVP.status).where(
                    and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member_id)
                )
            )
            row = rsvp_result.first()
            my_rsvp = row[0] if row else None

        # Roster with member info
        roster_result = await db.execute(
            select(EventRSVP, Member).join(Member, EventRSVP.member_id == Member.id).where(
                EventRSVP.event_id == event_id
            ).order_by(Member.last_name)
        )
        roster_rows = roster_result.all()

        attending = [(r, m) for r, m in roster_rows if r.status == "attending"]
        declined = [(r, m) for r, m in roster_rows if r.status == "declined"]
        pending = [(r, m) for r, m in roster_rows if r.status == "pending"]

        all_day = (event.date_start.hour == 0 and event.date_start.minute == 0
                   and (not event.date_end or (event.date_end.hour == 0 and event.date_end.minute == 0)))
        
        all_members = await db.scalars(select(Member).where(Member.status.in_(["active", "recruit"])).order_by(Member.last_name))
        members = all_members.all()

    rsvp_locked = bool(event.rsvp_deadline and _now_ct() > event.rsvp_deadline)

    return templates.TemplateResponse("pages/event_detail.html", {
        "members": members,
        "request": request,
        "user": user,
        "is_admin": _is_admin(user),
        "event": event,
        "icon": _get_icon(event.category),
        "category_label": CATEGORY_LABELS.get(event.category, event.category),
        "date_display": _format_range(event.date_start, event.date_end, all_day),
        "my_rsvp": my_rsvp,
        "rsvp_locked": rsvp_locked,
        "attending": attending,
        "rank_abbr": RANK_ABBR,
        "declined": declined,
        "pending": pending,
        "documents": event.documents,
        "now": _now_ct(),
    })


# ─── RSVP Endpoints ─────────────────────────────────────────────────────────

@router.post("/api/events/{event_id}/rsvp", response_class=HTMLResponse)
@require_auth
async def submit_rsvp(request: Request, event_id: int):
    """Submit or update RSVP for an event."""
    user = request.session.get("user", {})
    form = await request.form()
    new_status = form.get("status", "attending")

    if new_status not in ("attending", "declined"):
        return HTMLResponse("Invalid status", status_code=400)

    async with async_session() as db:
        # Check event exists and RSVP is enabled/open
        event_result = await db.execute(select(Event).where(Event.id == event_id))
        event = event_result.scalar_one_or_none()
        if not event:
            return HTMLResponse("Event not found", status_code=404)
        if not event.rsvp_enabled:
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;font-size:13px;">RSVP is not enabled for this event.</div>'
            )
        if event.rsvp_deadline and _now_ct() > event.rsvp_deadline:
            return HTMLResponse(
                '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;font-size:13px;">RSVP deadline has passed.</div>'
            )

        # Get member_id
        member_result = await db.execute(
            select(Member.id).where(Member.nc_username == user.get("username", ""))
        )
        member_row = member_result.first()
        if not member_row:
            return HTMLResponse("Member not found", status_code=404)
        member_id = member_row[0]

        # Find or create RSVP
        rsvp_result = await db.execute(
            select(EventRSVP).where(
                and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member_id)
            )
        )
        rsvp = rsvp_result.scalar_one_or_none()

        if rsvp:
            rsvp.status = new_status
            rsvp.responded_at = datetime.utcnow()
            rsvp.updated_at = datetime.utcnow()
        else:
            rsvp = EventRSVP(
                event_id=event_id,
                member_id=member_id,
                status=new_status,
                responded_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(rsvp)

        await db.commit()

    # Return updated RSVP controls
    return _render_rsvp_controls(event_id, new_status)


def _render_rsvp_controls(event_id: int, current_status: str) -> HTMLResponse:
    """Render inline RSVP buttons."""
    attending_cls = "rsvp-btn-active" if current_status == "attending" else ""
    declined_cls = "rsvp-btn-active-danger" if current_status == "declined" else ""

    html = f"""
    <div class="rsvp-controls" id="rsvp-controls-{event_id}">
        <form hx-post="/api/events/{event_id}/rsvp" hx-target="#rsvp-controls-{event_id}" hx-swap="outerHTML" style="display:inline;">
            <input type="hidden" name="status" value="attending">
            <button type="submit" class="rsvp-btn rsvp-btn-attend {attending_cls}">
                ✅ Attending
            </button>
        </form>
        <form hx-post="/api/events/{event_id}/rsvp" hx-target="#rsvp-controls-{event_id}" hx-swap="outerHTML" style="display:inline;">
            <input type="hidden" name="status" value="declined">
            <button type="submit" class="rsvp-btn rsvp-btn-decline {declined_cls}">
                ❌ Declined
            </button>
        </form>
    </div>"""
    return HTMLResponse(html)


@router.get("/api/events/{event_id}/roster", response_class=HTMLResponse)
@require_auth
async def event_roster(request: Request, event_id: int):
    """Return RSVP roster HTML partial. Shows confirmed attendees for finalized events."""
    async with async_session() as db:
        # Check if event is finalized
        evt = await db.execute(select(Event).where(Event.id == event_id))
        event = evt.scalar_one_or_none()

        roster_result = await db.execute(
            select(EventRSVP, Member).join(Member, EventRSVP.member_id == Member.id).where(
                EventRSVP.event_id == event_id
            ).order_by(Member.last_name)
        )
        roster_rows = roster_result.all()

    if event and event.finalized_at:
        # Finalized: show only confirmed attendees
        confirmed = [(r, m) for r, m in roster_rows if r.attended]
        absent = [(r, m) for r, m in roster_rows if not r.attended]
        count = len(confirmed)
        names = ", ".join(m.display_name for _, m in confirmed) if confirmed else "—"
        html = f"""
        <div style="margin-bottom:12px;">
            <div style="font-weight:600;color:#27ae60;font-size:14px;margin-bottom:4px;">
                Confirmed Attendees ({count})
            </div>
            <div style="font-size:13px;color:#ccc;padding-left:8px;">{names}</div>
        </div>"""
        if absent:
            absent_names = ", ".join(m.display_name for _, m in absent)
            html += f"""
        <div style="margin-bottom:12px;">
            <div style="font-weight:600;color:#888;font-size:14px;margin-bottom:4px;">
                Did Not Attend ({len(absent)})
            </div>
            <div style="font-size:13px;color:#666;padding-left:8px;">{absent_names}</div>
        </div>"""
    else:
        # Not finalized: show RSVP breakdown
        attending = [(r, m) for r, m in roster_rows if r.status == "attending"]
        declined = [(r, m) for r, m in roster_rows if r.status == "declined"]
        pending = [(r, m) for r, m in roster_rows if r.status == "pending"]
        html = _render_roster_section("Attending", attending, "#27ae60")
        html += _render_roster_section("Declined", declined, "#e74c3c")
        html += _render_roster_section("Pending", pending, "#f39c12")

    return HTMLResponse(html)


def _render_roster_section(label: str, entries: list, color: str) -> str:
    count = len(entries)
    names = ", ".join(m.display_name for _, m in entries) if entries else "—"
    return f"""
    <div style="margin-bottom:12px;">
        <div style="font-weight:600;color:{color};font-size:14px;margin-bottom:4px;">
            {label} ({count})
        </div>
        <div style="font-size:13px;color:#ccc;padding-left:8px;">{names}</div>
    </div>"""


# ─── Event Creation ──────────────────────────────────────────────────────────

@router.post("/api/events/create", response_class=HTMLResponse)
@require_role("command", "s3", "admin")
async def create_event(request: Request):
    """Create a new event manually."""
    user = request.session.get("user", {})
    form = await request.form()

    title = form.get("title", "").strip()
    category = form.get("category", "other")
    location = form.get("location", "").strip() or None
    description = form.get("description", "").strip() or None
    training_block = form.get("training_block", "").strip()
    instructor_id = form.get("instructor_id", "").strip()

    # Parse split date + military time fields
    date_start_date = form.get("date_start_date", "").strip()
    date_start_time = form.get("date_start_time", "").strip()
    date_end_date = form.get("date_end_date", "").strip()
    date_end_time = form.get("date_end_time", "").strip()

    if not title or not date_start_date:
        return HTMLResponse(
            '<div style="padding:12px;background:#b71c1c;color:#fff;border-radius:6px;">Title and start date are required.</div>'
        )

    try:
        date_start = _parse_mil_datetime(date_start_date, date_start_time)
        date_end = _parse_mil_datetime(date_end_date, date_end_time) if date_end_date else None
    except ValueError:
        return HTMLResponse(
            '<div style="padding:12px;background:#b71c1c;color:#fff;border-radius:6px;">Invalid date/time format. Use HHMM for time (e.g. 0600).</div>'
        )

    if category not in VALID_CATEGORIES:
        category = "other"

    rsvp_on = category in RSVP_CATEGORIES
    warno_sched = _calc_warno_schedule(category, date_start)

    async with async_session() as db:
        event = Event(
            title=title,
            category=category,
            description=description,
            location=location,
            date_start=date_start,
            date_end=date_end,
            status="active",
            rsvp_enabled=rsvp_on,
            warno_scheduled_at=warno_sched,
            training_block=int(training_block) if training_block else None,
            instructor_id=int(instructor_id) if instructor_id else None,
            created_by=user.get("username", "unknown"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(event)
        await db.flush()

        # Create pending RSVPs only for RSVP-enabled events
        if rsvp_on:
            members_result = await db.execute(
                select(Member.id).where(
                    Member.status.in_(["active", "recruit", "Active", "Recruit"])
                )
            )
            for (mid,) in members_result.all():
                db.add(EventRSVP(
                    event_id=event.id,
                    member_id=mid,
                    status="pending",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                ))

        await db.commit()

    return HTMLResponse(
        '<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        f'✅ Event "{title}" created.</div>'
        '<script>setTimeout(()=>window.location.reload(),1000)</script>'
    )


# ─── Event Edit ──────────────────────────────────────────────────────────────

@router.post("/api/events/{event_id}/edit", response_class=HTMLResponse)
@require_role("command", "s3", "s1", "admin")
async def edit_event(request: Request, event_id: int):
    """Edit event details."""
    form = await request.form()

    import logging
    logger = logging.getLogger("events.edit")
    form_dict = {k: form[k] for k in form}
    logger.warning(f"EDIT event_id={event_id} form={form_dict}")

    async with async_session() as db:
        result = await db.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
        if not event:
            return HTMLResponse("Event not found", status_code=404)

        logger.warning(f"BEFORE: date_start={event.date_start}, date_end={event.date_end}")

        if form.get("title"):
            event.title = form["title"].strip()
        if form.get("category") and form["category"] in VALID_CATEGORIES:
            event.category = form["category"]
        if form.get("location") is not None:
            event.location = form["location"].strip() or None
        if form.get("description") is not None:
            event.description = form["description"].strip() or None
        if form.get("date_start_date"):
            try:
                event.date_start = _parse_mil_datetime(
                    form["date_start_date"].strip(),
                    form.get("date_start_time", "").strip()
                )
            except ValueError:
                pass
        if form.get("date_end_date"):
            try:
                event.date_end = _parse_mil_datetime(
                    form["date_end_date"].strip(),
                    form.get("date_end_time", "").strip()
                )
            except ValueError:
                pass
        if form.get("training_block") is not None:
            tb = form["training_block"].strip()
            event.training_block = int(tb) if tb else None
        if form.get("instructor_id") is not None:
            iid = form["instructor_id"].strip()
            event.instructor_id = int(iid) if iid else None

        # RSVP controls
        if "rsvp_enabled" in form.keys():
            event.rsvp_enabled = form.get("rsvp_enabled") == "on"
        if form.get("rsvp_deadline_date"):
            try:
                event.rsvp_deadline = _parse_mil_datetime(
                    form["rsvp_deadline_date"].strip(),
                    form.get("rsvp_deadline_time", "").strip()
                )
            except ValueError:
                pass
        elif "rsvp_deadline_date" in form.keys():
            # Explicitly cleared
            event.rsvp_deadline = None

        event.updated_at = datetime.utcnow()
        logger.warning(f"AFTER: date_start={event.date_start}, date_end={event.date_end}")
        await db.commit()

    return HTMLResponse(
        '<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        f'✅ Event updated. <a href="/events/{event_id}" style="color:#8f8;text-decoration:underline;">Refresh</a></div>'
        f'<script>window.location.href="/events/{event_id}";</script>'
    )


# ─── Dashboard Widget (updated to use DB with CalDAV fallback) ───────────────

@router.get("/api/events/upcoming", response_class=HTMLResponse)
@require_auth
async def upcoming_events(request: Request):
    """Dashboard widget — upcoming events from DB, with CalDAV fallback."""
    user = request.session.get("user", {})
    now = _now_ct()

    async with async_session() as db:
        # Try DB first — deduplicate recurring events (show next occurrence only)
        result = await db.execute(
            select(Event).where(Event.date_start >= now).order_by(Event.date_start).limit(50)
        )
        all_events = result.scalars().all()

        # Keep only the first (soonest) occurrence per title
        seen_titles = set()
        events = []
        for ev in all_events:
            if ev.title not in seen_titles:
                seen_titles.add(ev.title)
                events.append(ev)
            if len(events) >= 10:
                break

        if events:
            # Get member_id for RSVP badge
            member_result = await db.execute(
                select(Member.id).where(Member.nc_username == user.get("username", ""))
            )
            member_row = member_result.first()
            member_id = member_row[0] if member_row else None

            # Batch RSVP counts — 1 query instead of N
            event_ids = [e.id for e in events]
            rsvp_stats_map = {}
            if event_ids:
                rsvp_stats = await db.execute(
                    select(
                        EventRSVP.event_id,
                        EventRSVP.status,
                        func.count(EventRSVP.id)
                    ).where(EventRSVP.event_id.in_(event_ids))
                    .group_by(EventRSVP.event_id, EventRSVP.status)
                )
                for eid, status, cnt in rsvp_stats.all():
                    rsvp_stats_map.setdefault(eid, {})[status] = cnt

            # Batch user RSVPs — 1 query instead of N
            my_rsvps_map = {}
            if member_id and event_ids:
                my_rsvps = await db.execute(
                    select(EventRSVP.event_id, EventRSVP.status).where(
                        and_(EventRSVP.member_id == member_id, EventRSVP.event_id.in_(event_ids))
                    )
                )
                for eid, status in my_rsvps.all():
                    my_rsvps_map[eid] = status

            html_parts = []
            for event in events:
                counts = rsvp_stats_map.get(event.id, {})
                attending = counts.get("attending", 0)
                total = attending + counts.get("declined", 0) + counts.get("pending", 0)
                my_rsvp = my_rsvps_map.get(event.id)

                icon = _get_icon(event.category)
                all_day = (event.date_start.hour == 0 and event.date_start.minute == 0)
                date_str = _format_date(event.date_start, all_day)

                # Days-until badge
                delta = (event.date_start.date() - _now_ct().date()).days
                if delta == 0:
                    badge = '<span style="background:#c62828;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;">TODAY</span>'
                elif delta == 1:
                    badge = '<span style="background:#ef6c00;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;">TOMORROW</span>'
                elif delta <= 14:
                    badge = f'<span style="background:#2e7d32;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;">{delta}d</span>'
                elif delta <= 30:
                    badge = f'<span style="background:#1565c0;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;">{delta}d</span>'
                else:
                    badge = f'<span style="font-size:11px;color:#888;margin-left:8px;">{delta}d</span>'

                # RSVP badge
                rsvp_badge = ""
                if my_rsvp == "attending":
                    rsvp_badge = '<span style="background:#27ae60;color:#fff;font-size:10px;padding:2px 6px;border-radius:8px;margin-left:6px;">GOING</span>'
                elif my_rsvp == "declined":
                    rsvp_badge = '<span style="background:#e74c3c;color:#fff;font-size:10px;padding:2px 6px;border-radius:8px;margin-left:6px;">DECLINED</span>'
                elif my_rsvp == "pending":
                    rsvp_badge = '<span style="background:#f39c12;color:#000;font-size:10px;padding:2px 6px;border-radius:8px;margin-left:6px;">PENDING</span>'

                headcount = f'<div style="font-size:12px;color:#999;white-space:nowrap;flex-shrink:0;text-align:right;">{attending}/{total}<br>attending</div>'

                html_parts.append(f"""
                <a href="/events/{event.id}" style="text-decoration:none;color:inherit;">
                <div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.06);cursor:pointer;transition:background 0.2s;overflow:hidden;" onmouseover="this.style.background='rgba(255,255,255,0.03)'" onmouseout="this.style.background='transparent'">
                    <div style="font-size:24px;min-width:32px;flex-shrink:0;text-align:center;">{icon}</div>
                    <div style="flex:1;min-width:0;">
                        <div style="font-weight:600;">{event.title}</div>
                        <div style="font-size:13px;color:#aaa;">{date_str} {badge}{rsvp_badge}</div>
                    </div>
                    {headcount}
                </div>
                </a>""")

            if html_parts:
                return HTMLResponse("".join(html_parts))

    # ─── CalDAV fallback if DB is empty ─────────────────────
    return await _caldav_fallback_widget()


async def _caldav_fallback_widget() -> HTMLResponse:
    """Original CalDAV-only widget as fallback."""
    settings = get_settings()
    nc_url = settings.nc_url
    now = datetime.utcnow()
    start = now.strftime("%Y%m%dT%H%M%SZ")
    end = (now + timedelta(days=90)).strftime("%Y%m%dT%H%M%SZ")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(
                "REPORT", f"{nc_url}{CALENDAR_PATH}",
                content=REPORT_BODY.format(start=start, end=end),
                headers={"Depth": "1", "Content-Type": "application/xml"},
                auth=(CALENDAR_USER, CALENDAR_PASS),
            )
            resp.raise_for_status()

        window_start = now
        window_end = now + timedelta(days=90)
        events = _parse_events_ical(resp.text, window_start, window_end)

        if not events:
            return HTMLResponse('<p class="text-muted">No upcoming events.</p>')

        seen = set()
        unique = []
        for ev in events:
            if ev["summary"] not in seen:
                seen.add(ev["summary"])
                unique.append(ev)

        html_parts = []
        for ev in unique[:10]:
            all_day = ev["start"].hour == 0 and ev["start"].minute == 0
            date_str = _format_range(ev["start"], ev["end"], all_day)
            icon = _get_icon(_guess_category(ev["summary"]))
            delta = (ev["start"].date() - _now_ct().date()).days
            if delta == 0:
                badge = '<span style="background:#c62828;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;">TODAY</span>'
            elif delta == 1:
                badge = '<span style="background:#ef6c00;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;">TOMORROW</span>'
            elif delta <= 14:
                badge = f'<span style="background:#2e7d32;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;">{delta}d</span>'
            else:
                badge = f'<span style="font-size:11px;color:#888;margin-left:8px;">{delta}d</span>'

            html_parts.append(f"""
            <div style="display:flex;align-items:flex-start;gap:12px;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
                <div style="font-size:24px;min-width:32px;text-align:center;">{icon}</div>
                <div style="flex:1;">
                    <div style="font-weight:600;">{ev["summary"]}{badge}</div>
                    <div style="font-size:13px;color:#aaa;">{date_str}</div>
                </div>
            </div>""")

        return HTMLResponse("".join(html_parts))
    except Exception as e:
        return HTMLResponse(f'<p class="text-muted">⚠️ Could not load events: {e}</p>')


# ─── Pending Finalization Widget (Dashboard) ────────────────────────────────

@router.get("/api/events/pending-finalization", response_class=HTMLResponse)
@require_auth
async def pending_finalization_widget(request: Request):
    """Dashboard widget — past FTX/MCFTXs awaiting attendance confirmation."""
    user = request.session.get("user", {})
    roles = set(user.get("roles", []))

    # Only visible to Command/S3/S1
    if not (roles & {"command", "s3", "s1", "admin"}):
        return HTMLResponse("")

    now = _now_ct()
    async with async_session() as db:
        result = await db.execute(
            select(Event).where(
                Event.category.in_(["ftx", "mcftx"]),
                Event.date_start < now,
                Event.finalized_at == None,
            ).order_by(Event.date_start.desc()).limit(10)
        )
        events = result.scalars().all()

    if not events:
        return HTMLResponse("")

    html_parts = [
        '<div style="background:rgba(212,165,55,0.1);border:1px solid rgba(212,165,55,0.4);'
        'border-radius:8px;padding:16px;margin-bottom:24px;">'
        '<div style="font-family:\'Oswald\',sans-serif;font-size:0.8rem;color:#d4a537;'
        'text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px;">'
        f'⚠️ Awaiting Attendance Confirmation ({len(events)})</div>'
    ]

    for event in events:
        icon = _get_icon(event.category)
        all_day = (event.date_start.hour == 0 and event.date_start.minute == 0
                   and (not event.date_end or (event.date_end.hour == 0 and event.date_end.minute == 0)))
        date_str = _format_range(event.date_start, event.date_end, all_day)
        block_str = f' · Block {event.training_block}' if event.training_block else ''

        html_parts.append(f"""
        <a href="/events/{event.id}" style="text-decoration:none;color:inherit;">
        <div style="display:flex;align-items:center;gap:12px;padding:10px 12px;background:#0f3460;
                    border:1px solid rgba(212,165,55,0.3);border-radius:6px;margin-bottom:6px;
                    cursor:pointer;transition:border-color 0.2s;"
             onmouseover="this.style.borderColor='#d4a537'" onmouseout="this.style.borderColor='rgba(212,165,55,0.3)'">
            <div style="font-size:24px;">{icon}</div>
            <div style="flex:1;">
                <div style="font-weight:600;font-size:14px;">{event.title}</div>
                <div style="font-size:12px;color:#aaa;">{date_str}{block_str}</div>
            </div>
            <div style="font-size:12px;color:#d4a537;font-weight:600;">Confirm →</div>
        </div>
        </a>""")

    html_parts.append("</div>")
    return HTMLResponse("".join(html_parts))


# ─── WARNO Banner (Dashboard) ───────────────────────────────────────────────

@router.get("/api/events/warno-banner", response_class=HTMLResponse)
@require_auth
async def warno_banner(request: Request):
    """Render WARNO banner for the next FTX/MCFTX with an issued (or auto-issuing) WARNO."""
    user = request.session.get("user", {})
    now = _now_ct()

    async with async_session() as db:
        # Auto-issue any WARNOs whose scheduled time has passed
        pending_warnos = await db.execute(
            select(Event).where(
                Event.category.in_(["ftx", "mcftx"]),
                Event.date_start > now,
                Event.warno_scheduled_at != None,
                Event.warno_scheduled_at <= now,
                Event.warno_issued_at == None,
            )
        )
        for event in pending_warnos.scalars().all():
            event.warno_issued_at = now
            event.updated_at = now
        await db.commit()

        # Find next FTX/MCFTX with an issued WARNO
        result = await db.execute(
            select(Event).where(
                Event.category.in_(["ftx", "mcftx"]),
                Event.date_start > now,
                Event.warno_issued_at != None,
            ).order_by(Event.date_start)
        )
        event = result.scalars().first()

        if not event:
            return HTMLResponse("")  # No active WARNO — empty banner area

        # Get RSVP counts
        counts_result = await db.execute(
            select(
                EventRSVP.status,
                func.count(EventRSVP.id)
            ).where(EventRSVP.event_id == event.id)
            .group_by(EventRSVP.status)
        )
        counts = dict(counts_result.all())
        attending = counts.get("attending", 0)
        declined = counts.get("declined", 0)
        pending = counts.get("pending", 0)

        # Get user's RSVP status
        member_result = await db.execute(
            select(Member.id).where(Member.nc_username == user.get("username", ""))
        )
        member_row = member_result.first()
        my_rsvp = "pending"
        my_responded = ""
        if member_row:
            rsvp_result = await db.execute(
                select(EventRSVP).where(
                    and_(EventRSVP.event_id == event.id, EventRSVP.member_id == member_row[0])
                )
            )
            rsvp = rsvp_result.scalar_one_or_none()
            if rsvp:
                my_rsvp = rsvp.status
                if rsvp.responded_at:
                    local_responded = _to_cdt(rsvp.responded_at)
                    my_responded = local_responded.strftime("RSVP'd %b %d at %H%M %Z")

    # Calculate days until event
    delta_days = (event.date_start.date() - _now_ct().date()).days

    # OPORD countdown
    opord_html = ""
    if event.opord_target_date and not event.opord_issued_at:
        opord_delta = event.opord_target_date - now
        if opord_delta.total_seconds() > 0:
            od = opord_delta.days
            oh = opord_delta.seconds // 3600
            opord_html = f"""
            <div style="text-align:right;">
                <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">OPORD Drops In</div>
                <div style="font-family:'Oswald',sans-serif;font-size:1.4rem;font-weight:700;color:#e0e0e0;">{od}d {oh}h</div>
            </div>"""
    elif event.opord_issued_at:
        opord_html = """
        <div style="text-align:right;">
            <span style="background:#2e7d32;color:#fff;padding:4px 10px;border-radius:4px;font-size:12px;font-weight:600;">📋 OPORD PUBLISHED</span>
        </div>"""

    # Stage label
    if event.fragord_issued_at:
        stage_label = "📝 FRAGORD ISSUED"
        stage_color = "#ef6c00"
    elif event.opord_issued_at:
        stage_label = "📋 OPERATIONS ORDER"
        stage_color = "#2e7d32"
    else:
        stage_label = "⚡ WARNING ORDER"
        stage_color = "#d4a537"

    # Event date range
    all_day = event.date_start.hour == 0 and event.date_start.minute == 0
    date_display = _format_range(event.date_start, event.date_end, all_day)

    # Category label
    cat_label = CATEGORY_LABELS.get(event.category, event.category)
    cat_icon = _get_icon(event.category)

    # RSVP buttons
    attending_cls = "background:#2e7d32;color:#fff;border-color:#2e7d32;" if my_rsvp == "attending" else ""
    declined_cls = "color:#e74c3c;border-color:#e74c3c;" if my_rsvp == "declined" else ""

    rsvp_buttons = ""
    if event.rsvp_enabled:
        rsvp_locked = bool(event.rsvp_deadline and now > event.rsvp_deadline)
        if rsvp_locked:
            rsvp_buttons = '<div style="font-size:12px;color:#ef5350;">🔒 RSVP closed</div>'
        else:
            rsvp_buttons = f"""
            <div id="warno-rsvp" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <form onclick="event.stopPropagation();event.preventDefault();" hx-post="/api/events/{event.id}/rsvp" hx-swap="none" hx-on::after-request="htmx.ajax('GET','/api/events/warno-banner','#warno-banner-area')" style="display:inline;">
                    <input type="hidden" name="status" value="attending">
                    <button type="submit" onclick="event.stopPropagation();" style="padding:6px 16px;border-radius:4px;font-weight:600;font-size:13px;cursor:pointer;border:2px solid #2e7d32;background:transparent;color:#2e7d32;{attending_cls}">
                        ✓ Attending
                    </button>
                </form>
                <form onclick="event.stopPropagation();event.preventDefault();" hx-post="/api/events/{event.id}/rsvp" hx-swap="none" hx-on::after-request="htmx.ajax('GET','/api/events/warno-banner','#warno-banner-area')" style="display:inline;">
                    <input type="hidden" name="status" value="declined">
                    <button type="submit" onclick="event.stopPropagation();" style="padding:6px 16px;border-radius:4px;font-weight:600;font-size:13px;cursor:pointer;border:2px solid #666;background:transparent;color:#888;{declined_cls}">
                        ✗ Decline
                    </button>
                </form>
                <span style="font-size:12px;color:#888;">{my_responded}</span>
            </div>"""

    return HTMLResponse(f"""
    <a href="/events/{event.id}" style="text-decoration:none;color:inherit;display:block;">
    <div style="background:linear-gradient(135deg, rgba(15,52,96,0.9), rgba(26,26,46,0.95));border:2px solid {stage_color};border-radius:10px;padding:20px 24px;margin-bottom:24px;position:relative;overflow:hidden;cursor:pointer;transition:border-color 0.2s;" onmouseover="this.style.borderColor='#fff'" onmouseout="this.style.borderColor='{stage_color}'">
        <div style="position:absolute;top:0;left:0;right:0;height:3px;background:{stage_color};"></div>
        <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;">
            <div>
                <span style="font-family:'Oswald',sans-serif;font-size:0.8rem;font-weight:700;letter-spacing:0.12em;color:{stage_color};text-transform:uppercase;">{stage_label}</span>
                <div style="font-family:'Oswald',sans-serif;font-size:1.5rem;font-weight:700;color:#fff;margin-top:4px;">
                    {event.title}
                    <span style="background:#2e7d32;color:#fff;font-size:12px;padding:2px 8px;border-radius:10px;margin-left:8px;font-family:'Inter',sans-serif;font-weight:600;">{delta_days}d</span>
                </div>
            </div>
            {opord_html}
        </div>
        <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:16px;">
            <div>
                <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">Category</div>
                <div style="font-size:14px;color:#e0e0e0;font-weight:500;">{cat_icon} {cat_label}</div>
            </div>
            <div>
                <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">Dates</div>
                <div style="font-size:14px;color:#e0e0e0;font-weight:500;">{date_display}</div>
            </div>
            <div>
                <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">Location</div>
                <div style="font-size:14px;color:#e0e0e0;font-weight:500;">{event.location or 'TBD'}</div>
            </div>
            <div>
                <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">WARNO Released</div>
                <div style="font-size:14px;color:#e0e0e0;font-weight:500;">{event.warno_issued_at.strftime('%b %d, %Y') if event.warno_issued_at else 'N/A'}</div>
            </div>
        </div>
        <div style="margin-top:16px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.1);">
            {rsvp_buttons}
            <div style="font-size:13px;color:#aaa;margin-top:8px;">
                <strong style="color:#e0e0e0;">{attending}</strong> attending · {declined} declined · {pending} pending
            </div>
        </div>
    </div>
    </a>
    """)


# ─── WARNO Issue / Schedule Controls ────────────────────────────────────────

@router.post("/api/events/{event_id}/issue-warno", response_class=HTMLResponse)
@require_auth
@require_role("command", "s3", "admin")
async def issue_warno(request: Request, event_id: int):
    """Immediately issue a WARNO for an event."""
    now = datetime.utcnow()
    async with async_session() as db:
        result = await db.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
        if not event:
            return HTMLResponse("Event not found", status_code=404)
        event.warno_issued_at = now
        event.updated_at = now
        await db.commit()

    return HTMLResponse(
        '<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        '⚡ WARNO issued. Banner is now live on all dashboards.</div>'
        '<script>setTimeout(()=>window.location.reload(),1500)</script>'
    )


@router.post("/api/events/{event_id}/schedule-warno", response_class=HTMLResponse)
@require_auth
@require_role("command", "s3", "admin")
async def schedule_warno(request: Request, event_id: int):
    """Set or update the WARNO scheduled issuance date."""
    form = await request.form()
    sched_date = form.get("warno_date", "").strip()
    sched_time = form.get("warno_time", "").strip()

    if not sched_date:
        return HTMLResponse(
            '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;font-size:13px;">Date required.</div>'
        )

    try:
        scheduled = _parse_mil_datetime(sched_date, sched_time)
    except ValueError:
        return HTMLResponse(
            '<div style="padding:8px;background:#b71c1c;color:#fff;border-radius:6px;font-size:13px;">Invalid date/time.</div>'
        )

    async with async_session() as db:
        result = await db.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
        if not event:
            return HTMLResponse("Event not found", status_code=404)
        event.warno_scheduled_at = scheduled
        event.updated_at = datetime.utcnow()
        await db.commit()

    return HTMLResponse(
        f'<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        f'📅 WARNO scheduled for {_to_cdt(scheduled).strftime("%d %b %Y · %H%M %Z")}.</div>'
        f'<script>setTimeout(()=>window.location.reload(),1500)</script>'
    )


@router.post("/api/events/{event_id}/issue-opord", response_class=HTMLResponse)
@require_auth
@require_role("command", "s3", "admin")
async def issue_opord(request: Request, event_id: int):
    """Mark OPORD as published."""
    now = datetime.utcnow()
    async with async_session() as db:
        result = await db.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
        if not event:
            return HTMLResponse("Event not found", status_code=404)
        event.opord_issued_at = now
        event.updated_at = now
        await db.commit()

    return HTMLResponse(
        '<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        '📋 OPORD published. Banner updated.</div>'
        '<script>setTimeout(()=>window.location.reload(),1500)</script>'
    )


@router.post("/api/events/{event_id}/issue-fragord", response_class=HTMLResponse)
@require_auth
@require_role("command", "s3", "admin")
async def issue_fragord(request: Request, event_id: int):
    """Mark FRAGORD as issued."""
    now = datetime.utcnow()
    async with async_session() as db:
        result = await db.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
        if not event:
            return HTMLResponse("Event not found", status_code=404)
        event.fragord_issued_at = now
        event.updated_at = now
        await db.commit()

    return HTMLResponse(
        '<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        '📝 FRAGORD issued. Banner updated.</div>'
        '<script>setTimeout(()=>window.location.reload(),1500)</script>'
    )


# ─── PP-074a: Post-FTX Attendance Confirmation ──────────────────────────────

@router.post("/api/events/{event_id}/attendance/{rsvp_id}", response_class=HTMLResponse)
@require_role("command", "s3", "s1", "admin")
async def toggle_attendance(request: Request, event_id: int, rsvp_id: int):
    """Toggle attended flag for a specific RSVP."""
    async with async_session() as db:
        result = await db.execute(
            select(EventRSVP).where(
                and_(EventRSVP.id == rsvp_id, EventRSVP.event_id == event_id)
            )
        )
        rsvp = result.scalar_one_or_none()
        if not rsvp:
            return HTMLResponse("RSVP not found", status_code=404)

        # Check event is not finalized
        ev_result = await db.execute(select(Event).where(Event.id == event_id))
        event = ev_result.scalar_one_or_none()
        if event and event.finalized_at:
            return HTMLResponse(
                '<div style="color:#ef5350;font-size:13px;">🔒 Event is finalized — attendance locked.</div>'
            )

        rsvp.attended = not rsvp.attended
        rsvp.updated_at = datetime.utcnow()
        new_state = rsvp.attended
        await db.commit()

        # Get member name for display
        m_result = await db.execute(select(Member).where(Member.id == rsvp.member_id))
        member = m_result.scalar_one_or_none()
        name = member.display_name if member else f"Member #{rsvp.member_id}"

    check = "☑" if new_state else "☐"
    color = "#27ae60" if new_state else "#888"
    return HTMLResponse(f"""
    <div id="att-row-{rsvp_id}" style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
        <button hx-post="/api/events/{event_id}/attendance/{rsvp_id}"
                hx-target="#att-row-{rsvp_id}" hx-swap="outerHTML"
                style="background:none;border:none;font-size:20px;cursor:pointer;color:{color};padding:0;line-height:1;">{check}</button>
        <span style="color:{'#e0e0e0' if new_state else '#888'};font-size:14px;">{name}</span>
    </div>""")


@router.post("/api/events/{event_id}/walk-in", response_class=HTMLResponse)
@require_role("command", "s3", "s1", "admin")
async def add_walk_in(request: Request, event_id: int):
    """Add a walk-in member to the event roster."""
    form = await request.form()
    member_id = form.get("member_id")
    if not member_id:
        return HTMLResponse("No member selected", status_code=400)

    async with async_session() as db:
        # Check event exists and is not finalized
        ev_result = await db.execute(select(Event).where(Event.id == event_id))
        event = ev_result.scalar_one_or_none()
        if not event:
            return HTMLResponse("Event not found", status_code=404)
        if event.finalized_at:
            return HTMLResponse(
                '<div style="color:#ef5350;font-size:13px;">🔒 Event is finalized.</div>'
            )

        # Check not already on roster
        existing = await db.execute(
            select(EventRSVP).where(
                and_(EventRSVP.event_id == event_id, EventRSVP.member_id == int(member_id))
            )
        )
        if existing.scalar_one_or_none():
            return HTMLResponse(
                '<div style="color:#f39c12;font-size:13px;">Already on roster.</div>'
            )

        rsvp = EventRSVP(
            event_id=event_id,
            member_id=int(member_id),
            status="attending",
            attended=True,
            responded_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(rsvp)
        await db.flush()

        # Get member name
        m_result = await db.execute(select(Member).where(Member.id == int(member_id)))
        member = m_result.scalar_one_or_none()
        name = member.display_name if member else f"Member #{member_id}"
        rsvp_id = rsvp.id

        await db.commit()

    return HTMLResponse(f"""
    <div style="color:#27ae60;font-size:13px;margin-bottom:8px;">✅ {name} added as walk-in</div>
    <div id="att-row-{rsvp_id}" style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
        <button hx-post="/api/events/{event_id}/attendance/{rsvp_id}"
                hx-target="#att-row-{rsvp_id}" hx-swap="outerHTML"
                style="background:none;border:none;font-size:20px;cursor:pointer;color:#27ae60;padding:0;line-height:1;">☑</button>
        <span style="color:#e0e0e0;font-size:14px;">{name}</span>
    </div>
    <script>setTimeout(()=>window.location.reload(),1500)</script>""")


# ─── PP-074a: Attendance Roster Partial ──────────────────────────────────────

@router.get("/api/events/{event_id}/attendance-roster", response_class=HTMLResponse)
@require_role("command", "s3", "s1", "admin")
async def attendance_roster(request: Request, event_id: int):
    """Return the attendance confirmation checklist partial."""
    async with async_session() as db:
        ev_result = await db.execute(select(Event).where(Event.id == event_id))
        event = ev_result.scalar_one_or_none()
        if not event:
            return HTMLResponse("Event not found", status_code=404)

        # Get all RSVPs with member info
        roster_result = await db.execute(
            select(EventRSVP, Member).join(Member, EventRSVP.member_id == Member.id).where(
                EventRSVP.event_id == event_id
            ).order_by(Member.last_name)
        )
        roster_rows = roster_result.all()

        # Get active members NOT on the roster (for walk-in dropdown)
        rostered_ids = {r.member_id for r, _ in roster_rows}
        available_result = await db.execute(
            select(Member).where(
                and_(
                    Member.status.in_(["active", "recruit", "Active", "Recruit"]),
                    ~Member.id.in_(rostered_ids) if rostered_ids else True,
                )
            ).order_by(Member.last_name)
        )
        available = available_result.scalars().all()

    rows_html = []
    for rsvp, member in roster_rows:
        check = "☑" if rsvp.attended else "☐"
        color = "#27ae60" if rsvp.attended else "#888"
        text_color = "#e0e0e0" if rsvp.attended else "#888"
        rows_html.append(f"""
        <div id="att-row-{rsvp.id}" style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
            <button hx-post="/api/events/{event_id}/attendance/{rsvp.id}"
                    hx-target="#att-row-{rsvp.id}" hx-swap="outerHTML"
                    style="background:none;border:none;font-size:20px;cursor:pointer;color:{color};padding:0;line-height:1;">{check}</button>
            <span style="color:{text_color};font-size:14px;">{member.display_name}</span>
        </div>""")

    # Walk-in dropdown
    options = "".join(
        f'<option value="{m.id}">{m.display_name}</option>' for m in available
    )
    walkin_html = f"""
    <div style="margin-top:16px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.1);">
        <form hx-post="/api/events/{event_id}/walk-in" hx-target="#walkin-result" hx-swap="innerHTML"
              style="display:flex;gap:8px;align-items:center;">
            <select name="member_id" style="flex:1;padding:6px 10px;background:#16213e;border:1px solid #2a2a4a;border-radius:4px;color:#e0e0e0;font-size:13px;">
                <option value="">— Add Walk-In —</option>
                {options}
            </select>
            <button type="submit" style="padding:6px 14px;background:#d4a537;color:#1a1a2e;border:none;border-radius:4px;font-weight:600;cursor:pointer;font-size:13px;">+ Add</button>
        </form>
        <div id="walkin-result" style="margin-top:8px;"></div>
    </div>""" if available else ""

    return HTMLResponse(
        f'<div>{"".join(rows_html)}</div>{walkin_html}'
    )


# ─── PP-074b: Event Finalization ─────────────────────────────────────────────

@router.post("/api/events/{event_id}/finalize", response_class=HTMLResponse)
@require_role("command", "s3", "s1", "admin")
async def finalize_event(request: Request, event_id: int):
    """Finalize event — set complete, record finalization, auto-credit TRADOC."""
    user = request.session.get("user", {})
    user_roles = set(user.get("roles", []))

    if not (user_roles & {"command", "s3", "s1", "admin"}):
        return HTMLResponse(
            '<div style="color:#ef5350;font-size:13px;">❌ Only Command, S3, or S1 can finalize events.</div>'
        )

    async with async_session() as db:
        result = await db.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
        if not event:
            return HTMLResponse("Event not found", status_code=404)
        if event.finalized_at:
            return HTMLResponse(
                '<div style="color:#f39c12;font-size:13px;">Already finalized.</div>'
            )

        now = datetime.utcnow()
        username = user.get("username", "unknown")

        # Set finalization
        event.status = "complete"
        event.finalized_at = now
        event.finalized_by = username
        event.updated_at = now

        # PP-074c: Auto-credit TRADOC
        credit_summary = ""
        if event.category in ("ftx", "mcftx"):
            credit_summary = await _auto_credit_tradoc(db, event)

        await db.commit()

    return HTMLResponse(
        f'<div style="padding:12px;background:#1b5e20;color:#fff;border-radius:6px;">'
        f'✅ Event finalized by {username}.'
        f'{" " + credit_summary if credit_summary else ""}'
        f'</div>'
        f'<script>setTimeout(()=>window.location.reload(),1500)</script>'
    )


# ─── Unfinalize Event (Command/S1 only) ─────────────────────────────────────

@router.post("/api/events/{event_id}/unfinalize", response_class=HTMLResponse)
@require_role("command", "s1", "admin")
async def unfinalize_event(request: Request, event_id: int):
    """Reopen a finalized event so attendance can be edited. Command/S1 only."""
    user = request.session.get("user", {})
    user_roles = set(user.get("roles", []))

    if not (user_roles & {"command", "s1", "admin"}):
        return HTMLResponse(
            '<div style="color:#ef5350;font-size:13px;">❌ Only Command or S1 can reopen finalized events.</div>'
        )

    async with async_session() as db:
        result = await db.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
        if not event:
            return HTMLResponse("Event not found", status_code=404)
        if not event.finalized_at:
            return HTMLResponse(
                '<div style="color:#f39c12;font-size:13px;">Event is not finalized.</div>'
            )

        username = user.get("username", "unknown")

        # Clear finalization
        event.finalized_at = None
        event.finalized_by = None
        event.status = "active"
        event.updated_at = datetime.utcnow()

        await db.commit()

    return HTMLResponse(
        f'<div style="padding:12px;background:#e65100;color:#fff;border-radius:6px;">'
        f'🔓 Event reopened by {username}. Attendance roster is now editable.'
        f'</div>'
        f'<script>setTimeout(()=>window.location.reload(),1500)</script>'
    )


# ─── PP-074c: Auto-Credit TRADOC on Finalization ────────────────────────────

BLOCK_0_ITEMS = [19, 20, 21]  # FOB Setup, Guard Duty, Stand-To
BLOCK_ITEMS = {
    1: [1, 2, 3, 4],           # Customs, D&C, Gear Review, Medical
    2: [5, 6, 7, 8, 9],        # Weapons Fam, BRM, Rifle Qual, Drills, UoF
    3: [10, 11, 12],            # Comms, Convoy, Land Nav
    4: [13, 14, 15, 16, 17, 18],  # React Ambush, H&A, IMT, Patrol, React Contact, Recon
}


async def _auto_credit_tradoc(db, event: Event) -> str:
    """Auto-credit TRADOC items for all attendees. Returns summary string."""
    # Determine items to credit
    items_to_credit = list(BLOCK_0_ITEMS)
    if event.training_block and event.training_block in BLOCK_ITEMS:
        items_to_credit.extend(BLOCK_ITEMS[event.training_block])

    # Get attendees
    attendees_result = await db.execute(
        select(EventRSVP.member_id).where(
            and_(EventRSVP.event_id == event.id, EventRSVP.attended == True)
        )
    )
    attendee_ids = [r[0] for r in attendees_result.all()]

    if not attendee_ids:
        return "No attendees to credit."

    # Get existing tradoc records for these members+items
    existing_result = await db.execute(
        select(MemberTradoc.member_id, MemberTradoc.item_id).where(
            and_(
                MemberTradoc.member_id.in_(attendee_ids),
                MemberTradoc.item_id.in_(items_to_credit),
            )
        )
    )
    existing = {(r[0], r[1]) for r in existing_result.all()}

    credited = 0
    members_credited = set()
    ftx_date = event.date_start.date() if hasattr(event.date_start, 'date') else event.date_start

    for mid in attendee_ids:
        for item_id in items_to_credit:
            if (mid, item_id) in existing:
                continue
            db.add(MemberTradoc(
                member_id=mid,
                item_id=item_id,
                signed_off_by="auto",
                ftx_date=ftx_date,
                notes=f"Auto-credited: {event.title}",
            ))
            credited += 1
            members_credited.add(mid)

    # Update ftx_count and last_ftx for attendees
    for mid in attendee_ids:
        # Count all attended FTX/MCFTX
        count_result = await db.execute(
            select(func.count(EventRSVP.id)).join(Event).where(
                and_(
                    EventRSVP.member_id == mid,
                    EventRSVP.attended == True,
                    Event.category.in_(["ftx", "mcftx"]),
                )
            )
        )
        cnt = count_result.scalar() or 0

        last_result = await db.execute(
            select(func.max(Event.date_start)).join(EventRSVP).where(
                and_(
                    EventRSVP.member_id == mid,
                    EventRSVP.attended == True,
                    Event.category.in_(["ftx", "mcftx"]),
                )
            )
        )
        last_dt = last_result.scalar()
        last_date = last_dt.date() if last_dt and hasattr(last_dt, 'date') else last_dt

        await db.execute(
            select(Member).where(Member.id == mid)
        )
        # Update directly
        await db.execute(
            update(Member).where(Member.id == mid).values(
                ftx_count=cnt,
                last_ftx=last_date,
                updated_at=datetime.utcnow(),
            )
        )

    return f"Credited {credited} items across {len(members_credited)} members."
