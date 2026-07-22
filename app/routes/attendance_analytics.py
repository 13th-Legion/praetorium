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

# Use the canonical rank map (single source of truth). The previous local copy
# was keyed "e4" (lowercase, no dash) while Member.rank_grade is "E-4", so the
# lookups silently returned "" — AND the values had drifted (E-1=PV2 not RCT,
# E-4=SPC not CPL, missing E-8M). Importing fixes both bugs at once.
from app.constants import RANK_ABBR

TEAM_LABELS = {
    "alpha": "Aquila", "aquila": "Aquila", "bravo": "Bravo", "charlie": "Charlie",
    "delta": "Delta", "echo": "Echo", "foxtrot": "Foxtrot",
}

# Positive-RSVP statuses: current code uses "attending", legacy imports used "accepted"
POSITIVE_RSVP = {"attending", "accepted"}


def _normalize_team(team: str | None) -> str | None:
    """Normalize team name to lowercase for consistent matching."""
    return team.lower() if team else None


def _has_access(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & {"command", "s3", "s1", "admin", "leader"})


def _events_since_join(events: list, join_date) -> list:
    """Return only events whose start date is on or after the member's join date."""
    if not join_date:
        return events
    for evt in events:
        # date_start is datetime; join_date is date — compare as dates
        pass
    return [e for e in events if e.date_start.date() >= join_date]


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
                "ftx_conducted": 0,
                "avg_attendance": 0,
                "avg_rate": 0,
                "no_shows": [],
                "trend": [], "trend_svg": {"points": []},
                "heat_rows": [], "hist_buckets": [], "hist_max": 0,
                "top_streaks": [], "superlatives": {},
            })

        event_ids = [e.id for e in events]
        total_events = len(events)

        # "FTXs Conducted" headline = 13th Legion FTXs only (exclude Pre-13th
        # historical events, which exist purely to credit pre-formation service).
        ftx_conducted = len([e for e in events if "Pre-13th" not in (e.title or "")])

        # Get all active/recruit members (excludes inactive, separated, blacklisted)
        members_result = await db.execute(
            select(Member).where(
                Member.status.in_(["active", "recruit"]),
                # On-leave members are exempt from attendance expectations — exclude
                # them from analytics so they don't drag down rates while on leave.
                Member.on_leave == False,  # noqa: E712
            )
        )
        all_members = members_result.scalars().all()
        roster_strength = len(all_members)
        active_member_ids = {m.id for m in all_members}

        # Get all RSVPs for finalized events, then drop RSVPs from non-active members
        # so per-event/team/headline stats never count people no longer in the unit.
        rsvps_result = await db.execute(
            select(EventRSVP).where(EventRSVP.event_id.in_(event_ids))
        )
        all_rsvps = [r for r in rsvps_result.scalars().all() if r.member_id in active_member_ids]

        # === Per-Event Stats === (13th Legion events only; Pre-13th excluded
        # so AVG HEADCOUNT / AVG RATE reflect actual 13th attendance, not
        # historical events scored against the current roster.)
        thirteenth_events = [e for e in events if "Pre-13th" not in (e.title or "")]
        thirteenth_event_ids = {e.id for e in thirteenth_events}
        event_stats = []
        for evt in thirteenth_events:
            evt_rsvps = [r for r in all_rsvps if r.event_id == evt.id]
            attended = len([r for r in evt_rsvps if r.attended])
            rsvp_attending = len([r for r in evt_rsvps if r.status in POSITIVE_RSVP])
            declined = len([r for r in evt_rsvps if r.status == "declined"])
            no_show = len([r for r in evt_rsvps if r.status in POSITIVE_RSVP and not r.attended])

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

        n_event_stats = len(event_stats)
        avg_attendance = round(sum(e["attended"] for e in event_stats) / n_event_stats, 1) if n_event_stats else 0
        avg_rate = round(sum(e["rate"] for e in event_stats) / n_event_stats) if n_event_stats else 0

        # === Per-Member Stats ===
        member_stats = []
        for m in all_members:
            # Eligible events = finalized events on or after this member's join date
            eligible_events = _events_since_join(events, m.join_date)
            eligible_count = len(eligible_events)
            eligible_event_ids = {e.id for e in eligible_events}

            # Only count RSVPs/attendance for events the member was eligible for.
            # Pre-join attended records (bad imports / guest appearances) would
            # otherwise push the numerator above the denominator -> rate >100%.
            m_rsvps = [
                r for r in all_rsvps
                if r.member_id == m.id and r.event_id in eligible_event_ids
            ]
            attended_count = len([r for r in m_rsvps if r.attended])
            rsvp_yes_count = len([r for r in m_rsvps if r.status in POSITIVE_RSVP])
            # No-shows ONLY count for 13th Legion events. Pre-13th attendance is
            # historical *credit* with no real RSVP/check-in data, so a backfilled
            # "missed" month must never register as a no-show.
            no_show_count = len([
                r for r in m_rsvps
                if r.status in POSITIVE_RSVP and not r.attended
                and r.event_id in thirteenth_event_ids
            ])

            # Attendance rate = attended (in eligible window) / eligible events.
            rate = min(round(attended_count / eligible_count * 100), 100) if eligible_count else 0

            # Last attended (within eligible window)
            attended_event_ids = [r.event_id for r in m_rsvps if r.attended]
            last_attended = None
            if attended_event_ids:
                last_evt = next((e for e in events if e.id in attended_event_ids), None)
                if last_evt:
                    from app.routes.events import _to_cdt
                    last_attended = _to_cdt(last_evt.date_start).strftime("%b %Y")

            rank = RANK_ABBR.get(m.rank_grade, "")
            team_normalized = _normalize_team(m.team)
            member_stats.append({
                "id": m.id,
                "name": f"{rank} {m.last_name}" if rank else m.last_name,
                "full_name": f"{m.first_name} {m.last_name}",
                "callsign": m.callsign or "",
                "team": TEAM_LABELS.get(team_normalized, m.team or "Unassigned"),
                "team_key": team_normalized,
                "attended": attended_count,
                "total": eligible_count,
                "rate": rate,
                "no_shows": no_show_count,
                "last_attended": last_attended or "Never",
                "status": m.status,
                "join_date": m.join_date,
            })

        member_stats.sort(key=lambda x: x["rate"], reverse=True)

        # === Per-Team Stats ===
        team_stats = []
        for team_key, team_label in TEAM_LABELS.items():
            team_members = [m for m in all_members if _normalize_team(m.team) == team_key]
            if not team_members:
                continue
            team_member_ids = {m.id for m in team_members}

            # Sum attended and eligible (possible) per member, respecting join dates
            team_attended_total = 0
            team_possible_total = 0
            for tm in team_members:
                eligible = _events_since_join(events, tm.join_date)
                eligible_ids = {e.id for e in eligible}
                tm_rsvps = [r for r in all_rsvps if r.event_id in eligible_ids and r.member_id == tm.id]
                team_attended_total += len([r for r in tm_rsvps if r.attended])
                team_possible_total += len(eligible)

            rate = round(team_attended_total / team_possible_total * 100) if team_possible_total else 0

            team_stats.append({
                "team": team_label,
                "members": len(team_members),
                "total_attended": team_attended_total,
                "possible": team_possible_total,
                "rate": rate,
            })

        team_stats.sort(key=lambda x: x["rate"], reverse=True)

        # === No-Show Report ===
        no_shows = [m for m in member_stats if m["no_shows"] > 0]
        no_shows.sort(key=lambda x: x["no_shows"], reverse=True)

        # ============================================================
        # === VISUALIZATIONS (13th Legion events only) ===============
        # ============================================================
        from app.routes.events import _to_cdt
        import calendar as _cal

        # Chronological 13th-era events with headcount
        viz_events = sorted(thirteenth_events, key=lambda e: e.date_start)
        att_by_evt = {}
        for e in viz_events:
            att_by_evt[e.id] = len([r for r in all_rsvps if r.event_id == e.id and r.attended])

        # --- 1) Monthly trend line (headcount over time) ---
        trend = []
        for e in viz_events:
            ld = _to_cdt(e.date_start)
            trend.append({
                "label": ld.strftime("%b %y"),
                "full_date": ld.strftime("%d %b %Y").lstrip("0"),
                "count": att_by_evt[e.id],
                "is_mcftx": e.category == "mcftx",
                "id": e.id,
            })
        trend_max = max([t["count"] for t in trend], default=0)

        # Build an SVG polyline path for the trend (viewBox 1000x260, padding 40)
        trend_svg = {"points": [], "max": trend_max, "w": 1000, "h": 260, "pad": 40}
        n_pts = len(trend)
        if n_pts > 1 and trend_max > 0:
            plot_w = trend_svg["w"] - 2 * trend_svg["pad"]
            plot_h = trend_svg["h"] - 2 * trend_svg["pad"]
            for i, t in enumerate(trend):
                x = trend_svg["pad"] + (plot_w * i / (n_pts - 1))
                y = trend_svg["pad"] + plot_h - (plot_h * t["count"] / trend_max)
                trend_svg["points"].append({
                    "x": round(x, 1), "y": round(y, 1),
                    "count": t["count"], "label": t["label"],
                    "full_date": t["full_date"], "is_mcftx": t["is_mcftx"],
                })
            trend_svg["polyline"] = " ".join(f"{p['x']},{p['y']}" for p in trend_svg["points"])
            # area path (close down to baseline)
            base_y = trend_svg["pad"] + plot_h
            first = trend_svg["points"][0]; last = trend_svg["points"][-1]
            trend_svg["area"] = (
                f"M {first['x']},{base_y} "
                + " ".join(f"L {p['x']},{p['y']}" for p in trend_svg["points"])
                + f" L {last['x']},{base_y} Z"
            )
            # y-axis gridlines (0, 25, 50, 75, 100% of max)
            trend_svg["gridlines"] = []
            for frac in (0, 0.25, 0.5, 0.75, 1.0):
                val = round(trend_max * frac)
                gy = trend_svg["pad"] + plot_h - (plot_h * frac)
                trend_svg["gridlines"].append({"y": round(gy, 1), "val": val})

        # --- 2) Year x Month heatmap (headcount intensity) ---
        # 11 FTX months (no August = Family Day), but show all for clarity
        heat = {}  # year -> {month -> count}
        for e in viz_events:
            ld = _to_cdt(e.date_start)
            heat.setdefault(ld.year, {})
            # if two events in one month (e.g. extra FTX), keep the higher headcount
            prev = heat[ld.year].get(ld.month, 0)
            heat[ld.year][ld.month] = max(prev, att_by_evt[e.id])
        heat_years = sorted(heat.keys())
        heat_rows = []
        for y in heat_years:
            cells = []
            for mo in range(1, 13):
                cnt = heat[y].get(mo)
                intensity = round(cnt / trend_max, 3) if (cnt and trend_max) else 0
                cells.append({
                    "month": _cal.month_abbr[mo],
                    "count": cnt,
                    "intensity": intensity,
                    "is_aug": mo == 8,
                })
            heat_rows.append({"year": y, "cells": cells})

        # --- 3) Attendance-rate distribution histogram ---
        buckets = [
            {"label": "0-19%", "lo": 0, "hi": 19, "count": 0, "color": "#ef5350"},
            {"label": "20-39%", "lo": 20, "hi": 39, "count": 0, "color": "#ff7043"},
            {"label": "40-59%", "lo": 40, "hi": 59, "count": 0, "color": "#f39c12"},
            {"label": "60-79%", "lo": 60, "hi": 79, "count": 0, "color": "#9ccc65"},
            {"label": "80-100%", "lo": 80, "hi": 100, "count": 0, "color": "#4caf50"},
        ]
        for m in member_stats:
            for b in buckets:
                if b["lo"] <= m["rate"] <= b["hi"]:
                    b["count"] += 1
                    break
        hist_max = max([b["count"] for b in buckets], default=0)

        # --- 4) Iron Man board: streaks of consecutive attended events ---
        # Walk each member's eligible events in chrono order, track longest + current streak
        evt_chrono = sorted(events, key=lambda e: e.date_start)  # includes pre-13th for OG streaks
        rsvp_idx = {}
        for r in all_rsvps:
            rsvp_idx[(r.member_id, r.event_id)] = r
        streak_board = []
        for m in all_members:
            elig = _events_since_join(evt_chrono, m.join_date)
            longest = cur = 0
            for e in elig:
                r = rsvp_idx.get((m.id, e.id))
                if r and r.attended:
                    cur += 1
                    longest = max(longest, cur)
                else:
                    cur = 0
            rank = RANK_ABBR.get(m.rank_grade, "")
            if longest > 0:
                streak_board.append({
                    "id": m.id,
                    "name": f"{rank} {m.last_name}" if rank else m.last_name,
                    "callsign": m.callsign or "",
                    "current": cur,
                    "longest": longest,
                })
        streak_board.sort(key=lambda x: (x["current"], x["longest"]), reverse=True)
        top_streaks = streak_board[:8]

        # --- 5) Superlatives / fun callouts ---
        superlatives = {}
        # Iron Man = highest current streak
        if streak_board:
            iron = max(streak_board, key=lambda x: x["longest"])
            superlatives["iron_man"] = iron
        # Best & worst attended event
        if trend:
            best_evt = max(trend, key=lambda t: t["count"])
            worst_evt = min(trend, key=lambda t: t["count"])
            superlatives["best_event"] = best_evt
            superlatives["worst_event"] = worst_evt
        # Perfect attendance count (100%, min 3 eligible to qualify)
        perfect = [m for m in member_stats if m["rate"] == 100 and m["total"] >= 3]
        superlatives["perfect_count"] = len(perfect)
        superlatives["perfect"] = perfect[:6]
        # Recent momentum: avg of last 3 vs prior 3 events
        if len(trend) >= 6:
            recent3 = sum(t["count"] for t in trend[-3:]) / 3
            prior3 = sum(t["count"] for t in trend[-6:-3]) / 3
            superlatives["momentum_recent"] = round(recent3, 1)
            superlatives["momentum_prior"] = round(prior3, 1)
            superlatives["momentum_delta"] = round(recent3 - prior3, 1)
        # Seasonal: best & worst month by avg headcount
        month_tot = {}
        for e in viz_events:
            ld = _to_cdt(e.date_start)
            month_tot.setdefault(ld.month, []).append(att_by_evt[e.id])
        month_avg = {mo: sum(v) / len(v) for mo, v in month_tot.items()}
        if month_avg:
            bm = max(month_avg, key=month_avg.get); wm = min(month_avg, key=month_avg.get)
            superlatives["best_month"] = {"name": _cal.month_name[bm], "avg": round(month_avg[bm], 1)}
            superlatives["worst_month"] = {"name": _cal.month_name[wm], "avg": round(month_avg[wm], 1)}

    return templates.TemplateResponse("pages/attendance_analytics.html", {
        "request": request,
        "user": user,
        "events": events,
        "member_stats": member_stats,
        "team_stats": team_stats,
        "event_stats": event_stats,
        "total_events": total_events,
        "ftx_conducted": ftx_conducted,
        "avg_attendance": avg_attendance,
        "avg_rate": avg_rate,
        "roster_strength": roster_strength,
        "no_shows": no_shows,
        "trend": trend,
        "trend_svg": trend_svg,
        "heat_rows": heat_rows,
        "hist_buckets": buckets,
        "hist_max": hist_max,
        "top_streaks": top_streaks,
        "superlatives": superlatives,
    })
