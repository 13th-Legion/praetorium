"""PP-045: Recruiting & Onboarding Analytics.

Companion to the FTX Attendance Analytics page. Visualizes the full
recruiting funnel: where members came in (intake by month), onboarding
document completion, patch (graduation) rate, attrition / separations,
cohort survival by join year, recruiter credit, and time-to-patch.

NOTE ON DATA REALITY (read before trusting numbers):
- There is no separate "applicant" table. The pipeline is tracked entirely
  on the `members` row via `status`, signed-doc timestamps, `patch_date`,
  and `separation_date`. So "applications" == people who were entered as
  recruits. People who didn't make it show up as inactive/separated/
  blacklisted, not as a distinct "rejected/timed-out applicant" record.
- The application-fee fields (app_fee_status/method/amount) are NOT
  populated in production (all 'pending', method NULL). We surface that
  honestly rather than charting empty data.
"""

from datetime import datetime, date
from collections import defaultdict, Counter

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth import require_auth, get_current_user
from app.database import async_session
from app.models.member import Member
from app.models.events import Event, EventRSVP
from app.models.recruiting import Recruiter, DocumentSignature, SeparationLog

# The 13th Legion was founded January 2024. Anyone with a join_date before this
# is an OG who carried over from a predecessor unit (Vanguard) — their intake
# predates the unit and is irrelevant to 13th recruiting funnel analysis, so we
# exclude them from all date/cohort/funnel math below.
FOUNDING_DATE = date(2024, 1, 1)

# Positive-RSVP statuses (mirrors attendance_analytics): a member "made it" to
# an FTX if they have a check-in (attended=True) on a finalized 13th-era event.
POSITIVE_RSVP = {"attending", "accepted"}

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Statuses that represent "did not stay" outcomes
LEFT_STATUSES = {"inactive", "separated", "blacklisted"}
STAYED_STATUSES = {"active"}
IN_PROGRESS_STATUSES = {"recruit"}

STATUS_META = {
    "active":      {"label": "Active",      "color": "#4caf50"},
    "recruit":     {"label": "Recruit",     "color": "#42a5f5"},
    "inactive":    {"label": "Inactive",    "color": "#888888"},
    "separated":   {"label": "Separated",   "color": "#f39c12"},
    "blacklisted": {"label": "Blacklisted", "color": "#ef5350"},
}

SEP_REASON_COLOR = {
    "voluntary":   "#42a5f5",
    "involuntary": "#ef5350",
    "inactivity":  "#888888",
    "blacklisted": "#9c27b0",
}


def _has_access(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & {"command", "s1", "admin", "leader"})


def _ym(d) -> str:
    return d.strftime("%Y-%m") if d else None


def _month_label(ym: str) -> str:
    try:
        return datetime.strptime(ym, "%Y-%m").strftime("%b %y")
    except Exception:
        return ym


@router.get("/api/s1/recruiting-analytics", response_class=HTMLResponse)
@require_auth
async def recruiting_analytics(request: Request):
    """Recruiting & Onboarding Analytics dashboard (S1)."""
    user = get_current_user(request)
    if not _has_access(user):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    async with async_session() as db:
        all_members = (await db.execute(select(Member))).scalars().all()
        recruiters = (await db.execute(select(Recruiter))).scalars().all()
        sep_logs = (await db.execute(select(SeparationLog))).scalars().all()
        doc_sigs = (await db.execute(select(DocumentSignature))).scalars().all()
        ftx_events = (await db.execute(
            select(Event).where(
                Event.category.in_(["ftx", "mcftx"]),
                Event.finalized_at.isnot(None),
            )
        )).scalars().all()
        all_rsvps = (await db.execute(select(EventRSVP))).scalars().all()

    # ---- Founding-date filter (request: ditch pre-13th intake) ----
    # Keep only members who joined on/after the 13th's founding for all funnel
    # analysis. Members with no join_date are kept (we can't prove they predate
    # the unit) but won't appear in date-based charts.
    pre_founding = [m for m in all_members if m.join_date and m.join_date < FOUNDING_DATE]
    members = [m for m in all_members if not (m.join_date and m.join_date < FOUNDING_DATE)]
    pre_founding_n = len(pre_founding)

    # ---- "Made it to a single FTX" set (request #4) ----
    # 13th-era finalized FTX/MCFTX events only (exclude Pre-13th titled events,
    # same convention as attendance_analytics). A member "made it" if they have
    # a check-in (attended) on any such event.
    ftx13_ids = {e.id for e in ftx_events if "Pre-13th" not in (e.title or "")}
    attended_member_ids = {
        r.member_id for r in all_rsvps
        if r.event_id in ftx13_ids and r.attended
    }

    total = len(members)

    if total == 0:
        return templates.TemplateResponse("pages/recruiting_analytics.html", {
            "request": request, "user": user, "total": 0,
        })

    # ---------- Headline status distribution ----------
    status_counter = Counter(m.status for m in members)
    status_cards = []
    for st in ["active", "recruit", "inactive", "separated", "blacklisted"]:
        meta = STATUS_META.get(st, {"label": st.title(), "color": "#888"})
        status_cards.append({
            "key": st, "label": meta["label"], "color": meta["color"],
            "count": status_counter.get(st, 0),
            "pct": round(100 * status_counter.get(st, 0) / total, 1),
        })

    active_n = status_counter.get("active", 0)
    recruit_n = status_counter.get("recruit", 0)
    left_n = sum(status_counter.get(s, 0) for s in LEFT_STATUSES)

    # ---------- Intake by month (joins) ----------
    join_by_month = defaultdict(int)
    for m in members:
        if m.join_date:
            join_by_month[_ym(m.join_date)] += 1
    # Separations by month
    sep_by_month = defaultdict(int)
    for m in members:
        if m.separation_date:
            sep_by_month[_ym(m.separation_date)] += 1

    all_months = sorted(set(join_by_month) | set(sep_by_month))
    # Keep it readable: only show from first 13th-era join (2024) forward,
    # but never drop a month that has activity.
    flow_rows = []
    for ym in all_months:
        flow_rows.append({
            "ym": ym, "label": _month_label(ym),
            "joins": join_by_month.get(ym, 0),
            "seps": sep_by_month.get(ym, 0),
            "net": join_by_month.get(ym, 0) - sep_by_month.get(ym, 0),
        })
    max_flow = max([max(r["joins"], r["seps"]) for r in flow_rows], default=1) or 1

    # Net cumulative line for sparkline
    running = 0
    net_series = []
    for r in flow_rows:
        running += r["net"]
        net_series.append(running)

    # ---------- Onboarding funnel (signed docs -> patch) ----------
    def signed(attr):
        return sum(1 for m in members if getattr(m, attr) is not None)

    funnel = [
        {"label": "Entered as recruit", "count": total, "color": "#42a5f5",
         "note": "every row in the system"},
        {"label": "Signed NDA", "count": signed("nda_signed_at"), "color": "#5c9ce6"},
        {"label": "Signed Waiver", "count": signed("waiver_signed_at"), "color": "#6fae8f"},
        {"label": "Signed Code of Conduct", "count": signed("code_of_conduct_signed_at"), "color": "#8bbf6a"},
        {"label": "Signed Bylaws", "count": signed("bylaws_signed_at"), "color": "#b0c44d"},
        {"label": "Signed Activity Policy", "count": signed("activity_policy_signed_at"), "color": "#d4a537"},
        {"label": "Patched (graduated)", "count": sum(1 for m in members if m.patch_date), "color": "#4caf50",
         "note": "completed TRADOC, full member"},
    ]
    funnel_max = funnel[0]["count"] or 1
    for f in funnel:
        f["pct"] = round(100 * f["count"] / funnel_max, 1)

    fully_signed = sum(
        1 for m in members
        if all(getattr(m, a) is not None for a in [
            "nda_signed_at", "waiver_signed_at", "code_of_conduct_signed_at",
            "bylaws_signed_at", "activity_policy_signed_at"])
    )
    patched_n = sum(1 for m in members if m.patch_date)

    # ---------- Cohort outcomes by join year ----------
    cohort = defaultdict(lambda: Counter())
    for m in members:
        if m.join_date:
            cohort[m.join_date.year][m.status] += 1
    cohort_rows = []
    for yr in sorted(cohort):
        c = cohort[yr]
        yr_total = sum(c.values())
        active = c.get("active", 0)
        cohort_rows.append({
            "year": yr, "total": yr_total,
            "active": active,
            "recruit": c.get("recruit", 0),
            "inactive": c.get("inactive", 0),
            "separated": c.get("separated", 0),
            "blacklisted": c.get("blacklisted", 0),
            "retention": round(100 * active / yr_total, 1) if yr_total else 0,
        })

    # ---------- Separations breakdown ----------
    sep_reason_counter = Counter()
    for m in members:
        if m.separation_date or m.status in LEFT_STATUSES:
            reason = (m.separation_reason or "").strip().lower()
            # Normalize freeform reasons to a bucket
            if "blacklist" in reason or m.status == "blacklisted":
                sep_reason_counter["blacklisted"] += 1
            elif "involuntary" in reason:
                sep_reason_counter["involuntary"] += 1
            elif "inactiv" in reason or m.status == "inactive":
                sep_reason_counter["inactivity"] += 1
            elif "voluntary" in reason or "quit" in reason:
                sep_reason_counter["voluntary"] += 1
            else:
                sep_reason_counter["unspecified"] += 1
    sep_total = sum(sep_reason_counter.values()) or 1
    sep_reasons = []
    for reason, cnt in sep_reason_counter.most_common():
        sep_reasons.append({
            "reason": reason.title(),
            "count": cnt,
            "pct": round(100 * cnt / sep_total, 1),
            "color": SEP_REASON_COLOR.get(reason, "#bbbbbb"),
        })

    # ---------- Tenure / time-to-patch ----------
    patch_days = [
        (m.patch_date - m.join_date).days
        for m in members
        if m.patch_date and m.join_date and (m.patch_date - m.join_date).days >= 0
    ]
    tenure_days = [
        (m.separation_date - m.join_date).days
        for m in members
        if m.separation_date and m.join_date and (m.separation_date - m.join_date).days >= 0
    ]

    def _stats(vals):
        if not vals:
            return None
        s = sorted(vals)
        n = len(s)
        med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
        return {"count": n, "min": s[0], "max": s[-1],
                "median": round(med, 1), "mean": round(sum(s) / n, 1)}

    time_to_patch = _stats(patch_days)
    tenure = _stats(tenure_days)

    # ---------- Recruiter credit ----------
    recruiter_rows = []
    for r in recruiters:
        recruiter_rows.append({
            "name": r.display_name, "user": r.nc_username,
            "active": r.is_active, "load": r.current_load,
            "max_load": r.max_load, "recruited": r.total_recruited,
        })
    recruiter_rows.sort(key=lambda x: (x["load"], x["recruited"]), reverse=True)

    # ---------- Flags ----------
    veterans = sum(1 for m in members if m.is_veteran)

    # ---------- "Never made it to a single FTX" (request #4) ----------
    # Of post-founding members, who has zero FTX check-ins ever? This is the
    # recruiting funnel's real leak: people who signed up and never showed.
    ever_attended_n = sum(1 for m in members if m.id in attended_member_ids)
    never_attended = [m for m in members if m.id not in attended_member_ids]
    never_attended_n = len(never_attended)
    ever_attended_pct = round(100 * ever_attended_n / total, 1) if total else 0

    # Status breakdown of the never-showed cohort
    never_by_status = Counter(m.status for m in never_attended)
    never_status_rows = []
    for st in ["recruit", "inactive", "separated", "blacklisted", "active"]:
        cnt = never_by_status.get(st, 0)
        if cnt == 0:
            continue
        meta = STATUS_META.get(st, {"label": st.title(), "color": "#888"})
        never_status_rows.append({
            "label": meta["label"], "color": meta["color"], "count": cnt,
            "pct": round(100 * cnt / never_attended_n, 1) if never_attended_n else 0,
        })

    # Attendance reach by status: of each status, what share ever showed to an FTX
    attend_by_status = []
    status_totals = Counter(m.status for m in members)
    for st in ["active", "recruit", "inactive", "separated", "blacklisted"]:
        st_total = status_totals.get(st, 0)
        if st_total == 0:
            continue
        st_attended = sum(1 for m in members if m.status == st and m.id in attended_member_ids)
        meta = STATUS_META.get(st, {"label": st.title(), "color": "#888"})
        attend_by_status.append({
            "label": meta["label"], "color": meta["color"],
            "attended": st_attended, "total": st_total,
            "pct": round(100 * st_attended / st_total, 1) if st_total else 0,
        })

    # ---------- Data-gap callouts (honesty layer) ----------
    # NOTE: the $50 application fee IS tracked — but via the PayPal webhook
    # (PP-126) matching payments against Deck pipeline cards, NOT the
    # members.app_fee_status column (which stays 'pending' by design). Paying
    # the fee is a hard gate to getting on the roster, so by definition every
    # member/recruit shown here has paid. We therefore do NOT flag fee tracking
    # as a gap; we surface it as a 100%-complete prerequisite instead.
    recruiter_field_populated = any(m.assigned_recruiter for m in members)

    data_gaps = []
    if not recruiter_field_populated:
        data_gaps.append(
            "Per-member recruiter assignment (members.assigned_recruiter) is "
            "blank for everyone, and recruiters.total_recruited is 0 across the "
            "board — so we can't attribute joins to a specific recruiter yet.")

    # ---------- Funnel conversion summary ----------
    conv_recruit_to_patch = round(100 * patched_n / total, 1) if total else 0
    survival = round(100 * active_n / total, 1) if total else 0

    return templates.TemplateResponse("pages/recruiting_analytics.html", {
        "request": request, "user": user,
        "total": total,
        "active_n": active_n, "recruit_n": recruit_n, "left_n": left_n,
        "patched_n": patched_n, "fully_signed": fully_signed,
        "status_cards": status_cards,
        "flow_rows": flow_rows, "max_flow": max_flow, "net_series": net_series,
        "funnel": funnel, "funnel_max": funnel_max,
        "cohort_rows": cohort_rows,
        "sep_reasons": sep_reasons, "sep_total": sep_total,
        "time_to_patch": time_to_patch, "tenure": tenure,
        "recruiter_rows": recruiter_rows,
        "veterans": veterans,
        "conv_recruit_to_patch": conv_recruit_to_patch, "survival": survival,
        "data_gaps": data_gaps,
        "status_meta": STATUS_META,
        "pre_founding_n": pre_founding_n,
        "ever_attended_n": ever_attended_n, "ever_attended_pct": ever_attended_pct,
        "never_attended_n": never_attended_n,
        "never_status_rows": never_status_rows,
        "attend_by_status": attend_by_status,
        "ftx13_count": len(ftx13_ids),
    })
