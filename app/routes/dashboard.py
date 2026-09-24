"""Dashboard route — authenticated landing page."""
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_

from app.auth import require_auth
from app import database
from app.models.elections import Election, ElectionBallot
from app.models.member import Member
from app.models.events import Event
from app.models.s2_intel import IIR
from app.models.rank_history import RankHistory
from app.models.ribbons import MemberRibbon, RibbonCatalog
from app.routes.elections import _auto_advance
from app.services import ranks as _ranks
from app.services.ribbon_derive import derive_ribbons
from app.models.schedule import EventScheduleBlock
from app.models.events import EventRSVP

import logging
_log = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")

from zoneinfo import ZoneInfo
_CDT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")
def _fmt_ct_stored(dt):
    """Tag an already-naive-CT datetime with CT tz WITHOUT converting.
    For event date_start/date_end (stored naive wall-clock CT)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=_CDT) if dt.tzinfo is None else dt.astimezone(_CDT)


def _to_cdt(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_CDT)
templates.env.filters["cdt"] = _to_cdt
templates.env.filters["cdt_stored"] = _fmt_ct_stored

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
    async with database.async_session() as db:
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
    async with database.async_session() as db2:
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

    # Latest unit-wide IIRs for the intel card (S2 — front page shows last 2-3).
    latest_iirs = []
    async with database.async_session() as db3:
        _iirs = (await db3.execute(
            select(IIR)
            .where(IIR.status == "active", IIR.dissemination_tier == "unit_wide")
            .order_by(IIR.created_at.desc())
            .limit(3)
        )).scalars().all()
        for _i in _iirs:
            latest_iirs.append({
                "id": _i.id,
                "report_number": _i.report_number,
                "subject": _i.subject,
                "country_area": _i.country_area,
                "dtg": _i.dtg,
            })

    return templates.TemplateResponse("pages/dashboard.html", {
        "request": request,
        "user": user,
        "is_command": bool(roles & COMMAND_ROLES),
        "is_s1_lead": "s1_lead" in roles or bool(roles & {"command", "admin"}),
        "active_election": active_election,
        "election_winner_name": election_winner_name,
        "latest_aars": latest_aars,
        "latest_iirs": latest_iirs,
    })


def _member_name(m: Member) -> str:
    """Rank-abbr + last name (+ callsign) for feed rows."""
    abbr = _ranks.abbr_map().get(m.rank_grade, m.rank_grade or "")
    base = f"{abbr} {m.last_name}".strip()
    return base


@router.get("/api/dashboard/activity-feed", response_class=HTMLResponse)
@require_auth
async def activity_feed(request: Request):
    """Unit-wide Promotions & Awards feed (PP-253).

    Merges rank_history (promotions) + member_ribbons (manual/claim grants) +
    EVENT-DERIVED ribbons into one reverse-chronological stream.

    Derived ribbons are computed at render time and have no row and no
    awarded_at, so before 2026-09-21 they never appeared here at all -- the
    card looked frozen since the last manual grant (22 JUL 2026) even though
    FTX/instructor ribbons were being earned continuously. We now surface the
    event-derived codes using the date of the most recent triggering event.

    Only EVENT-derived codes are included (ftx, mcftx, instructor_ftx,
    instructor_online). Cert/TRADOC/rank-derived codes have no event date and
    are not discrete 'events', so inventing a date for them would be a lie.
    """
    items = []  # each: {dt, kind, icon, member_id, name, callsign, text, img}
    async with database.async_session() as db:
        # Promotions — skip the very first (initial) assignment where old_rank is null
        pr = await db.execute(
            select(RankHistory, Member)
            .join(Member, Member.id == RankHistory.member_id)
            .where(RankHistory.old_rank.is_not(None))
            .order_by(RankHistory.effective_date.desc())
            .limit(25)
        )
        for rh, m in pr.all():
            old_abbr = _ranks.abbr_map().get(rh.old_rank, rh.old_rank or "")
            new_abbr = _ranks.abbr_map().get(rh.new_rank, rh.new_rank or "")
            insig = _ranks.insignia_map().get(rh.new_rank)  # None for E-1/W-1 (no insignia)
            items.append({
                "dt": _to_cdt(rh.effective_date),
                "kind": "promotion",
                "icon": "\U0001F53C",  # up-triangle fallback
                "member_id": m.id,
                "name": _member_name(m),
                "callsign": m.callsign,
                "text": f"promoted {old_abbr} \u2192 {new_abbr}",
                "img": None,
                "rank_img": f"/static/img/ranks/{insig}" if insig else None,
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
                "dt": _to_cdt(mr.awarded_at),
                "kind": "award",
                "icon": "\U0001F396\uFE0F",  # medal
                "member_id": m.id,
                "name": _member_name(m),
                "callsign": m.callsign,
                "text": f"{verb} {cat.name}",
                "img": f"/static/img/ribbons/{cat.image}" if cat.image else None,
                "rank_img": None,
            })

        # ── Event-derived ribbons (no rows exist; computed at render time) ──
        # Dates come from Event.date_start, which is stored NAIVE WALL-CLOCK CT,
        # so it must be tagged with _fmt_ct_stored (NOT _to_cdt, which assumes
        # UTC and would shift every derived award by the offset).
        EVENT_DERIVED = {
            "ftx": ("ftx",),
            "mcftx": ("mcftx",),
            "instructor_ftx": ("ftx", "mcftx"),
            "instructor_online": ("online_training",),
        }
        cat_rows = (await db.execute(select(RibbonCatalog))).scalars().all()
        cat_by_code = {c.code: c for c in cat_rows}

        roster = (await db.execute(
            select(Member).where(Member.status.in_(("active", "recruit")))
        )).scalars().all()

        for m in roster:
            try:
                derived_list = await derive_ribbons(db, m)
            except Exception:
                _log.exception("derive_ribbons failed for member %s", m.id)
                continue
            for d in derived_list:
                code = d.get("code")
                cats = EVENT_DERIVED.get(code)
                if not cats:
                    continue  # cert/TRADOC/rank derived: no event date, not an event
                if code == "instructor_online":
                    # online_training records its instructor on the EVENT, not
                    # on a schedule block -- those events have no blocks at
                    # all, so a block join would always return None and this
                    # ribbon would never appear in the feed.
                    latest = (await db.execute(
                        select(func.max(Event.date_start))
                        .where(
                            Event.instructor_id == m.id,
                            Event.category.in_(cats),
                            Event.status != "cancelled",
                            func.coalesce(Event.date_end, Event.date_start) < datetime.now(),
                        )
                    )).scalar()
                elif code.startswith("instructor_"):
                    # ftx/mcftx instructors live on schedule blocks;
                    # events.instructor_id is NULL for every ftx row.
                    latest = (await db.execute(
                        select(func.max(Event.date_start))
                        .select_from(EventScheduleBlock)
                        .join(Event, Event.id == EventScheduleBlock.event_id)
                        .where(
                            EventScheduleBlock.instructor_id == m.id,
                            EventScheduleBlock.activity_type == "class",
                            Event.category.in_(cats),
                        )
                    )).scalar()
                else:
                    latest = (await db.execute(
                        select(func.max(Event.date_start))
                        .select_from(EventRSVP)
                        .join(Event, Event.id == EventRSVP.event_id)
                        .where(
                            EventRSVP.member_id == m.id,
                            EventRSVP.attended.is_(True),
                            Event.category.in_(cats),
                        )
                    )).scalar()
                if latest is None:
                    continue
                cat = cat_by_code.get(code)
                if cat is None:
                    continue
                items.append({
                    "dt": _fmt_ct_stored(latest),
                    "kind": "award",
                    "icon": "\U0001F396\uFE0F",
                    "member_id": m.id,
                    "name": _member_name(m),
                    "callsign": m.callsign,
                    "text": f"earned {cat.name}",
                    "img": f"/static/img/ribbons/{cat.image}" if cat.image else None,
                    "rank_img": None,
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
        d = it["dt"]
        datestr = d.strftime("%d %b").upper().lstrip("0") if d else ""
        cs = f' <span style="color:#888;font-style:italic;">\u201c{it["callsign"]}\u201d</span>' if it["callsign"] else ""
        thumb_src = it.get("rank_img") or it.get("img")
        thumb = (
            f'<img src="{thumb_src}" alt="" '
            f'style="width:24px;height:24px;object-fit:contain;flex-shrink:0;" '
            f'onerror="this.style.display=\'none\'">'
            if thumb_src else
            f'<span style="font-size:16px;width:24px;text-align:center;flex-shrink:0;">{it["icon"]}</span>'
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
    if len(items) <= 4:
        # Short list: no scroll needed.
        return HTMLResponse(f'<div>{rows}</div>')

    # Scrollable container. JS gently auto-scrolls until the user interacts
    # (wheel/touch/pointer/hover), then hands full manual scroll control over.
    # Rows duplicated once so the auto-scroll can loop seamlessly before handoff.
    return HTMLResponse(
        '<div class="feed-scroll" id="activity-feed-scroll">'
        f'<div class="feed-track">{rows}{rows}</div>'
        '</div>'
        '<script>(function(){'
        'var box=document.getElementById("activity-feed-scroll");'
        'if(!box)return;'
        'var track=box.querySelector(".feed-track");'
        'var half=function(){return track.scrollHeight/2;};'
        'var paused=false,manual=false,raf=null,last=null;'
        'var SPEED=18;'  # px/sec
        'function step(ts){'
        'if(last==null)last=ts;var dt=(ts-last)/1000;last=ts;'
        'if(!paused&&!manual){box.scrollTop+=SPEED*dt;'
        'if(box.scrollTop>=half()){box.scrollTop-=half();}}'
        'raf=requestAnimationFrame(step);}'
        'raf=requestAnimationFrame(step);'
        # hover pauses (resumes on leave) unless user took manual control
        'box.addEventListener("mouseenter",function(){paused=true;});'
        'box.addEventListener("mouseleave",function(){if(!manual)paused=false;});'
        # any real scroll input = permanent manual handoff
        'function takeover(){manual=true;paused=true;if(raf)cancelAnimationFrame(raf);}'
        'box.addEventListener("wheel",takeover,{passive:true});'
        'box.addEventListener("touchstart",takeover,{passive:true});'
        'box.addEventListener("pointerdown",takeover);'
        '})();</script>'
    )
