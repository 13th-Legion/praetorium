"""S3 Ops & Training API routes — FTX Builder, schedule management."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db, async_session
from app.models.events import Event, EventRSVP
from app.models.schedule import EventScheduleBlock
from app.models.member import Member
from app.models.training import TradocItem
from app.training_sites import TRAINING_SITES, get_site_maps
from fastapi.templating import Jinja2Templates
from app.constants import RANK_ABBR

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["s3"])

S3_ROLES = {"s3", "command", "admin"}
S2_ROLES = {"s2", "command", "admin"}

ACTIVITY_TYPES = [
    ("class", "Class"),
    ("mission", "Mission"),
    ("formation", "Formation"),
    ("meal", "Meal"),
    ("admin", "Admin"),
    ("break", "Break"),
    ("other", "Other"),
]

BLOCK_LABELS = {
    1: "Block 1 — Theory / Medical",
    2: "Block 2 — Weapons Qualification",
    3: "Block 3 — Supplemental (Comms / Land Nav)",
    4: "Block 4 — Combat Fundamentals",
}


def _has_s3_access(user: dict) -> bool:
    return bool(set(user.get("roles", [])) & S3_ROLES)


# ─── Dashboard Widgets ───────────────────────────────────────────────────────

@router.get("/api/s3/events-needing-planning")
@require_auth
async def events_needing_planning(request: Request, db: AsyncSession = Depends(get_db)):
    """HTMX partial: upcoming events that need schedule planning."""
    user = get_current_user(request)
    if not _has_s3_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    now = datetime.utcnow()

    # Get upcoming events we actually plan (FTX only — MCFTXs only when we host, external trainings are third-party)
    result = await db.execute(
        select(Event)
        .where(
            Event.date_start > now,
            Event.category.in_(["ftx"]),
            Event.status.notin_(["cancelled"]),
        )
        .order_by(Event.date_start)
        .limit(10)
    )
    events = result.scalars().all()

    if not events:
        return HTMLResponse(
            '<div style="color: #999; font-size: 14px; padding: 8px 0;">No upcoming events need planning.</div>'
        )

    # Check which events have schedule blocks
    event_ids = [e.id for e in events]
    block_counts_result = await db.execute(
        select(EventScheduleBlock.event_id, func.count(EventScheduleBlock.id))
        .where(EventScheduleBlock.event_id.in_(event_ids))
        .group_by(EventScheduleBlock.event_id)
    )
    block_counts = dict(block_counts_result.all())

    # RSVP counts
    rsvp_counts_result = await db.execute(
        select(EventRSVP.event_id, func.count(EventRSVP.id))
        .where(EventRSVP.event_id.in_(event_ids), EventRSVP.status == "attending")
        .group_by(EventRSVP.event_id)
    )
    rsvp_counts = dict(rsvp_counts_result.all())

    cat_icons = {"ftx": "🏕️", "mcftx": "⚔️", "external_training": "🎓"}

    rows = []
    for ev in events:
        blocks = block_counts.get(ev.id, 0)
        attending = rsvp_counts.get(ev.id, 0)
        icon = cat_icons.get(ev.category, "📅")
        days_out = (ev.date_start - now).days

        # Status badge
        if blocks == 0:
            badge = '<span style="background:#b71c1c;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">Needs Schedule</span>'
        else:
            badge = f'<span style="background:#1b5e20;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{blocks} blocks</span>'

        # Training block label
        block_label = ""
        if ev.training_block:
            block_label = f' · Block {ev.training_block}'

        rows.append(f'''
            <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid #2a2a3e;">
                <div style="display:flex;align-items:center;gap:10px;">
                    <span style="font-size:20px;">{icon}</span>
                    <div>
                        <a href="/api/s3/builder/{ev.id}" style="color:#e0e0e0;text-decoration:none;font-weight:500;font-size:14px;">{ev.title}</a>
                        <div style="color:#999;font-size:12px;">{ev.date_start.strftime("%b %d, %Y")}{block_label} · {attending} attending · {days_out}d out</div>
                    </div>
                </div>
                <div style="display:flex;align-items:center;gap:8px;">
                    {badge}
                    <a href="/api/s3/builder/{ev.id}" style="color:#d4a537;font-size:12px;text-decoration:none;">Build →</a>
                </div>
            </div>
        ''')

    return HTMLResponse("".join(rows))


@router.get("/api/s3/block-rotation")
@require_auth
async def block_rotation(request: Request, db: AsyncSession = Depends(get_db)):
    """HTMX partial: training block rotation status."""
    user = get_current_user(request)
    if not _has_s3_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    # Get the last 4 FTXs to show block rotation
    result = await db.execute(
        select(Event)
        .where(
            Event.category.in_(["ftx", "mcftx"]),
            Event.status.notin_(["cancelled"]),
            Event.date_start < datetime.utcnow(),
        )
        .order_by(Event.date_start.desc())
        .limit(6)
    )
    past_events = list(reversed(result.scalars().all()))

    # Get next upcoming FTX
    next_result = await db.execute(
        select(Event)
        .where(
            Event.category.in_(["ftx", "mcftx"]),
            Event.status.notin_(["cancelled"]),
            Event.date_start > datetime.utcnow(),
        )
        .order_by(Event.date_start)
        .limit(1)
    )
    next_event = next_result.scalar_one_or_none()

    rows = []
    for ev in past_events:
        block = ev.training_block
        label = BLOCK_LABELS.get(block, "No block assigned") if block else "No block assigned"
        rows.append(f'''
            <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #2a2a3e;font-size:13px;">
                <span style="color:#999;">{ev.date_start.strftime("%b %Y")} — {ev.title}</span>
                <span style="color:{"#d4a537" if block else "#666"};">{f"Block {block}" if block else "—"}</span>
            </div>
        ''')

    if next_event:
        block = next_event.training_block
        rows.append(f'''
            <div style="display:flex;justify-content:space-between;padding:8px 0;font-size:13px;border-top:2px solid #d4a537;margin-top:4px;">
                <span style="color:#d4a537;font-weight:600;">▶ Next: {next_event.date_start.strftime("%b %Y")} — {next_event.title}</span>
                <span style="color:#d4a537;font-weight:600;">{f"Block {block}" if block else "TBD"}</span>
            </div>
        ''')

    if not rows:
        return HTMLResponse('<div style="color:#666;font-size:13px;">No FTX history yet.</div>')

    return HTMLResponse("".join(rows))


# ─── FTX Builder ─────────────────────────────────────────────────────────────

@router.get("/api/s3/builder/{event_id}")
@require_auth
async def ftx_builder(request: Request, event_id: int, db: AsyncSession = Depends(get_db)):
    """FTX Builder page — schedule editor for an event."""
    user = get_current_user(request)
    if not _has_s3_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    # Get event
    event = await db.get(Event, event_id)
    if not event:
        return HTMLResponse("<h2>Event not found.</h2>", status_code=404)

    # Get existing schedule blocks
    blocks_result = await db.execute(
        select(EventScheduleBlock)
        .where(EventScheduleBlock.event_id == event_id)
        .order_by(EventScheduleBlock.day_number, EventScheduleBlock.sort_order, EventScheduleBlock.start_time)
    )
    blocks = blocks_result.scalars().all()

    # Get active members for instructor dropdown
    members_result = await db.execute(
        select(Member)
        .where(Member.status.in_(["active", "Active"]))
        .order_by(Member.rank_grade.desc(), Member.last_name)
    )
    members = members_result.scalars().all()

    # Calculate event days
    days = 1
    if event.date_end and event.date_start:
        delta = event.date_end.date() - event.date_start.date() if hasattr(event.date_end, 'date') else event.date_end - event.date_start
        if hasattr(delta, 'days'):
            days = max(delta.days + 1, 1) if hasattr(delta, 'days') else max(int(delta) + 1, 1)
        else:
            days = 1

    # Group blocks by day
    blocks_by_day = {}
    for b in blocks:
        if b.day_number not in blocks_by_day:
            blocks_by_day[b.day_number] = []
        blocks_by_day[b.day_number].append(b)

    # Member lookup for instructor names
    member_lookup = {m.id: m for m in members}

    # Training site maps
    site_maps = get_site_maps(event.training_site) if event.training_site else []

    # Get TRADOC items for dropdowns
    tradoc_result = await db.execute(
        select(TradocItem)
        .order_by(TradocItem.block, TradocItem.sort_order)
    )
    all_tradoc = tradoc_result.scalars().all()
    
    # Pre-filter tradoc items by event's training block (if set)
    # Plus block 0 (every FTX)
    tradoc_for_block = []
    if event.training_block:
        tradoc_for_block = [t for t in all_tradoc if t.block == event.training_block or t.block == 0]
    else:
        tradoc_for_block = all_tradoc

    return templates.TemplateResponse("pages/ftx_builder.html", {
        "request": request,
        "user": user,
        "event": event,
        "blocks": blocks,
        "blocks_by_day": blocks_by_day,
        "members": members,
        "member_lookup": member_lookup,
        "days": days,
        "activity_types": ACTIVITY_TYPES,
        "block_labels": BLOCK_LABELS,
        "rank_abbr": RANK_ABBR,
        "training_sites": TRAINING_SITES,
        "site_maps": site_maps,
        "tradoc_items": tradoc_for_block,
    })


@router.post("/api/s3/builder/{event_id}/set-training-site")
@require_auth
async def set_training_site(
    request: Request,
    event_id: int,
    training_site: str = Form(""),
):
    """Set or clear the training site for an event."""
    user = get_current_user(request)
    if not _has_s3_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    site_key = training_site.strip() if training_site else None
    if site_key and site_key not in TRAINING_SITES:
        site_key = None

    async with async_session() as db:
        event = await db.get(Event, event_id)
        if not event:
            return HTMLResponse('<div style="color:#b71c1c;">Event not found.</div>', status_code=404)
        event.training_site = site_key
        event.updated_at = datetime.utcnow()
        await db.commit()

    # Return updated maps section via HTMX
    if site_key:
        maps = get_site_maps(site_key)
        site = TRAINING_SITES[site_key]
        html = f'<div style="margin-top:8px;">'
        html += f'<div style="color:#999;font-size:13px;">📍 {site["address"]}</div>'
        html += '<div style="margin-top:6px;display:flex;gap:8px;flex-wrap:wrap;">'
        for m in maps:
            html += f'<a href="{m["url"]}" target="_blank" style="color:#d4a537;font-size:12px;border:1px solid #d4a537;padding:4px 10px;border-radius:4px;text-decoration:none;">🗺️ {m["label"]}</a>'
        html += '</div></div>'
        return HTMLResponse(html)
    else:
        return HTMLResponse('<div style="color:#666;font-size:13px;margin-top:8px;">No training site selected.</div>')


@router.post("/api/s3/builder/{event_id}/add-block")
@require_auth
async def add_schedule_block(
    request: Request,
    event_id: int,
    day_number: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(None),
    title: str = Form(...),
    activity_type: str = Form("class"),
    instructor_id: Optional[int] = Form(None),
    notes: str = Form(None),
):
    """Add a schedule block to an event."""
    user = get_current_user(request)
    if not _has_s3_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    # Validate time format
    start_time = start_time.strip().zfill(4)
    if end_time:
        end_time = end_time.strip().zfill(4)
    else:
        end_time = None

    async with async_session() as db:
        # Get max sort_order for this day
        max_order = await db.execute(
            select(func.max(EventScheduleBlock.sort_order))
            .where(EventScheduleBlock.event_id == event_id, EventScheduleBlock.day_number == day_number)
        )
        current_max = max_order.scalar() or 0

        block = EventScheduleBlock(
            event_id=event_id,
            day_number=day_number,
            start_time=start_time,
            end_time=end_time,
            title=title.strip(),
            activity_type=activity_type,
            instructor_id=instructor_id if instructor_id and instructor_id > 0 else None,
            notes=notes.strip() if notes else None,
            sort_order=current_max + 1,
            created_by=user.get("username", "unknown"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(block)
        await db.commit()

    # Redirect back to builder (HTMX will swap)
    return HTMLResponse(
        '<div style="color:#1b5e20;padding:8px;font-size:13px;">✅ Block added.</div>'
        f'<script>setTimeout(()=>window.location.href="/api/s3/builder/{event_id}",500)</script>'
    )


@router.delete("/api/s3/builder/{event_id}/block/{block_id}")
@require_auth
async def delete_schedule_block(request: Request, event_id: int, block_id: int):
    """Delete a schedule block."""
    user = get_current_user(request)
    if not _has_s3_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    async with async_session() as db:
        block = await db.get(EventScheduleBlock, block_id)
        if block and block.event_id == event_id:
            await db.delete(block)
            await db.commit()

    return HTMLResponse(
        '<div style="color:#d4a537;padding:8px;font-size:13px;">Block removed.</div>'
        f'<script>setTimeout(()=>window.location.href="/api/s3/builder/{event_id}",500)</script>'
    )


@router.post("/api/s3/builder/{event_id}/update-block/{block_id}")
@require_auth
async def update_schedule_block(
    request: Request,
    event_id: int,
    block_id: int,
    day_number: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(None),
    title: str = Form(...),
    activity_type: str = Form("class"),
    instructor_id: Optional[int] = Form(None),
    notes: str = Form(None),
):
    """Update an existing schedule block."""
    user = get_current_user(request)
    if not _has_s3_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    async with async_session() as db:
        block = await db.get(EventScheduleBlock, block_id)
        if not block or block.event_id != event_id:
            return HTMLResponse('<div style="color:#b71c1c;">Block not found.</div>', status_code=404)

        block.day_number = day_number
        block.start_time = start_time.strip().zfill(4)
        block.end_time = end_time.strip().zfill(4) if end_time else None
        block.title = title.strip()
        block.activity_type = activity_type
        block.instructor_id = instructor_id if instructor_id and instructor_id > 0 else None
        block.notes = notes.strip() if notes else None
        block.updated_at = datetime.utcnow()
        await db.commit()

    return HTMLResponse(
        '<div style="color:#1b5e20;padding:8px;font-size:13px;">✅ Block updated.</div>'
        f'<script>setTimeout(()=>window.location.href="/api/s3/builder/{event_id}",500)</script>'
    )


# ─── S2 Rally Point (temporary home until s2_ops.py) ────────────────────────

def _has_s2_access(user: dict) -> bool:
    return bool(set(user.get("roles", [])) & (S2_ROLES | S3_ROLES))


@router.post("/api/s2/rally-point/{event_id}")
@require_auth
async def set_rally_point(
    request: Request,
    event_id: int,
    rally_point: str = Form(...),
):
    """S2 sets the rally point for an event."""
    user = get_current_user(request)
    if not _has_s2_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied — S2/Command only.</div>', status_code=403)

    async with async_session() as db:
        event = await db.get(Event, event_id)
        if not event:
            return HTMLResponse('<div style="color:#b71c1c;">Event not found.</div>', status_code=404)
        event.rally_point = rally_point.strip() if rally_point.strip() else None
        event.rally_point_set_by = user.get("username", "unknown")
        event.rally_point_set_at = datetime.utcnow()
        event.updated_at = datetime.utcnow()
        await db.commit()

    if event.rally_point:
        return HTMLResponse(
            '<div style="color:#1b5e20;padding:8px;font-size:13px;">✅ Rally point set.</div>'
            f'<script>setTimeout(()=>window.location.reload(),500)</script>'
        )
    else:
        return HTMLResponse(
            '<div style="color:#d4a537;padding:8px;font-size:13px;">Rally point cleared.</div>'
            f'<script>setTimeout(()=>window.location.reload(),500)</script>'
        )


@router.get("/api/s2/events-needing-rally-point")
@require_auth
async def events_needing_rally_point(request: Request, db: AsyncSession = Depends(get_db)):
    """HTMX partial: upcoming FTXs with training site set but no rally point."""
    user = get_current_user(request)
    if not _has_s2_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    now = datetime.utcnow()
    result = await db.execute(
        select(Event)
        .where(
            Event.date_start > now,
            Event.category.in_(["ftx"]),
            Event.training_site.isnot(None),
            Event.rally_point.is_(None),
            Event.status.notin_(["cancelled"]),
        )
        .order_by(Event.date_start)
        .limit(10)
    )
    events = result.scalars().all()

    if not events:
        return HTMLResponse('<div style="color:#999;font-size:14px;padding:8px 0;">No events need rally points right now. 👍</div>')

    rows = []
    for ev in events:
        days_out = (ev.date_start - now).days
        site = TRAINING_SITES.get(ev.training_site, {})
        site_name = f"Site {site.get('name', ev.training_site)}" if site else ev.training_site
        rows.append(f'''
            <div style="padding:12px;border-bottom:1px solid #2a2a3e;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <span style="color:#e0e0e0;font-weight:500;font-size:14px;">{ev.title}</span>
                        <div style="color:#999;font-size:12px;">{ev.date_start.strftime("%b %d, %Y")} · {site_name} · {days_out}d out</div>
                    </div>
                    <span style="background:#b71c1c;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">Needs RP</span>
                </div>
                <form hx-post="/api/s2/rally-point/{ev.id}" hx-target="#rp-result-{ev.id}" hx-swap="innerHTML" style="margin-top:8px;display:flex;gap:8px;">
                    <input name="rally_point" placeholder="Rally point address / description" style="flex:1;background:#12121e;border:1px solid #444;color:#fff;padding:6px 10px;border-radius:4px;font-size:13px;" required>
                    <button type="submit" style="background:#d4a537;color:#000;border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-weight:600;font-size:13px;">Set RP</button>
                </form>
                <div id="rp-result-{ev.id}" style="margin-top:4px;"></div>
            </div>
        ''')

    return HTMLResponse("".join(rows))


# ─── Rally Time ──────────────────────────────────────────────────────────────

@router.post("/api/s3/rally-time/{event_id}")
@require_auth
async def set_rally_time(
    request: Request,
    event_id: int,
    rally_time: str = Form(""),
):
    """Set the rally point time for an event (HHMM format)."""
    user = get_current_user(request)
    if not _has_s3_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    async with async_session() as db:
        event = await db.get(Event, event_id)
        if not event:
            return HTMLResponse('<div style="color:#b71c1c;">Event not found.</div>', status_code=404)
        event.rally_point_time = rally_time.strip() if rally_time.strip() else None
        event.updated_at = datetime.utcnow()
        await db.commit()

    return HTMLResponse(
        '<div style="color:#1b5e20;padding:8px;font-size:13px;">✅ Rally time updated.</div>'
        '<script>setTimeout(()=>window.location.reload(),500)</script>'
    )


# ─── Radio Frequencies ───────────────────────────────────────────────────────

@router.post("/api/s3/frequencies/{event_id}")
@require_auth
async def set_frequencies(
    request: Request,
    event_id: int,
):
    """Set convoy and FOB radio frequencies for an event."""
    user = get_current_user(request)
    if not _has_s3_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    form = await request.form()

    async with async_session() as db:
        event = await db.get(Event, event_id)
        if not event:
            return HTMLResponse('<div style="color:#b71c1c;">Event not found.</div>', status_code=404)
        event.freq_convoy_primary = form.get("freq_convoy_primary", "").strip() or None
        event.freq_convoy_alternate = form.get("freq_convoy_alternate", "").strip() or None
        event.freq_fob_primary = form.get("freq_fob_primary", "").strip() or None
        event.freq_fob_alternate = form.get("freq_fob_alternate", "").strip() or None
        event.updated_at = datetime.utcnow()
        await db.commit()

    return HTMLResponse(
        '<div style="color:#1b5e20;padding:8px;font-size:13px;">✅ Frequencies updated.</div>'
        '<script>setTimeout(()=>window.location.reload(),500)</script>'
    )


# ─── SMEAC OPORD ─────────────────────────────────────────────────────────────

@router.post("/api/s3/opord/{event_id}")
@require_auth
async def save_opord(
    request: Request,
    event_id: int,
):
    """Save the 5-paragraph OPORD (SMEAC) for an event."""
    user = get_current_user(request)
    if not _has_s3_access(user):
        return HTMLResponse('<div style="color:#b71c1c;">Access denied.</div>', status_code=403)

    form = await request.form()

    async with async_session() as db:
        event = await db.get(Event, event_id)
        if not event:
            return HTMLResponse('<div style="color:#b71c1c;">Event not found.</div>', status_code=404)
        event.opord_situation = form.get("opord_situation", "").strip() or None
        event.opord_mission = form.get("opord_mission", "").strip() or None
        event.opord_execution = form.get("opord_execution", "").strip() or None
        event.opord_admin_logistics = form.get("opord_admin_logistics", "").strip() or None
        event.opord_command_signal = form.get("opord_command_signal", "").strip() or None
        event.updated_at = datetime.utcnow()
        await db.commit()

    return HTMLResponse(
        '<div style="color:#1b5e20;padding:8px;font-size:13px;">✅ OPORD saved.</div>'
        '<script>setTimeout(()=>window.location.reload(),500)</script>'
    )
