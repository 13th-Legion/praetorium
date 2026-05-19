"""Promotions Dashboard — Time-in-Grade weighted roster for Command/S1."""

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth import require_auth, get_current_user
from app.constants import RANK_ABBR, RANK_TITLE, COMMAND_ROLES, S1_ROLES
from app.database import async_session
from app.models.member import Member
from app.models.rank_history import RankHistory

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# ─── Rank ordering (low → high) ──────────────────────────────────────────────

RANK_ORDER = [
    "E-1", "E-2", "E-3", "E-4", "E-5", "E-6", "E-7", "E-8M", "E-8", "E-9",
    "W-1", "W-2", "W-3", "W-4", "W-5",
    "O-1", "O-2", "O-3", "O-4",
]

RANK_INDEX = {r: i for i, r in enumerate(RANK_ORDER)}


def _allowed_promotions(current_rank: str) -> list[tuple[str, str]]:
    """Return list of (grade, display_label) the member could be promoted to."""
    idx = RANK_INDEX.get(current_rank)
    if idx is None:
        return []
    choices = []
    # Allow promotion within same track (enlisted/warrant/officer), one or two steps up
    track_prefix = current_rank[0]  # 'E', 'W', or 'O'
    for r in RANK_ORDER[idx + 1:]:
        if r[0] == track_prefix:
            abbr = RANK_ABBR.get(r, r)
            title = RANK_TITLE.get(r, "")
            choices.append((r, f"{abbr} — {title}"))
    return choices


def _has_access(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & (COMMAND_ROLES | S1_ROLES))


def _format_tig(days: int) -> str:
    """Human-readable time-in-grade from day count."""
    if days < 0:
        return "—"
    years = days // 365
    remaining = days % 365
    months = remaining // 30
    d = remaining % 30
    parts = []
    if years:
        parts.append(f"{years}y")
    if months:
        parts.append(f"{months}m")
    parts.append(f"{d}d")
    return " ".join(parts)


@router.get("/api/s1/promotions", response_class=HTMLResponse)
@require_auth
async def promotions_dashboard(request: Request):
    """Render the Promotions dashboard."""
    user = get_current_user(request)
    if not _has_access(user):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    today = date.today()

    async with async_session() as db:
        # Get all active + recruit members
        result = await db.execute(
            select(Member).where(
                Member.status.in_(("active", "recruit")),
                Member.rank_grade.isnot(None),
            )
        )
        members = result.scalars().all()

        # Get most recent rank_history entry per member for last promotion date
        rh_result = await db.execute(
            select(RankHistory)
        )
        all_history = rh_result.scalars().all()

    # Build lookup: member_id → most recent promotion date
    last_promo: dict[int, date] = {}
    for rh in all_history:
        eff = rh.effective_date.date() if isinstance(rh.effective_date, datetime) else rh.effective_date
        if rh.member_id not in last_promo or eff > last_promo[rh.member_id]:
            last_promo[rh.member_id] = eff

    rows = []
    for m in members:
        promo_date = last_promo.get(m.id)
        # Fall back to patch_date if no promotion history
        if not promo_date:
            promo_date = m.patch_date

        if promo_date:
            tig_days = (today - promo_date).days
        else:
            tig_days = -1  # unknown

        # Non-promotable check
        non_promotable = False
        np_reason = None
        if m.non_promotable_until and m.non_promotable_until >= today:
            non_promotable = True
            np_reason = m.non_promotable_reason or "Non-promotable hold"

        rows.append({
            "id": m.id,
            "rank_grade": m.rank_grade,
            "rank_abbr": RANK_ABBR.get(m.rank_grade, m.rank_grade or "—"),
            "rank_index": RANK_INDEX.get(m.rank_grade, 99),
            "last_name": m.last_name or "",
            "first_name": m.first_name or "",
            "callsign": m.callsign or "—",
            "promo_date": promo_date,
            "promo_date_str": promo_date.strftime("%Y-%m-%d") if promo_date else "—",
            "tig_days": tig_days,
            "tig_display": _format_tig(tig_days) if tig_days >= 0 else "Unknown",
            "non_promotable": non_promotable,
            "np_reason": np_reason,
            "allowed_promotions": _allowed_promotions(m.rank_grade),
        })

    # Default sort: highest TIG first
    rows.sort(key=lambda r: (-r["tig_days"], r["rank_index"], r["last_name"]))

    return templates.TemplateResponse("pages/promotions.html", {
        "request": request,
        "user": user,
        "rows": rows,
        "today": today,
    })


@router.post("/api/s1/promotions/promote", response_class=HTMLResponse)
@require_auth
async def promote_member(request: Request):
    """Execute a promotion."""
    user = get_current_user(request)
    if not _has_access(user):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    form = await request.form()
    member_id = int(form.get("member_id", 0))
    new_rank = form.get("new_rank", "").strip()

    if not member_id or not new_rank:
        raise HTTPException(400, "Missing member_id or new_rank")

    if new_rank not in RANK_INDEX:
        raise HTTPException(400, f"Invalid rank: {new_rank}")

    async with async_session() as db:
        result = await db.execute(select(Member).where(Member.id == member_id))
        member = result.scalar_one_or_none()
        if not member:
            raise HTTPException(404, "Member not found")

        old_rank = member.rank_grade

        # Validate promotion direction
        old_idx = RANK_INDEX.get(old_rank, -1)
        new_idx = RANK_INDEX.get(new_rank, -1)
        if new_idx <= old_idx:
            raise HTTPException(400, f"Cannot promote {old_rank} → {new_rank} (not a higher rank)")

        # Check non-promotable hold
        today = date.today()
        if member.non_promotable_until and member.non_promotable_until >= today:
            raise HTTPException(400, f"Member is non-promotable until {member.non_promotable_until}")

        # Execute promotion
        member.rank_grade = new_rank

        # Auto-patch: E-1 → E-2+ sets patch_date and status
        if old_rank == "E-1" and new_rank != "E-1":
            if not member.patch_date:
                member.patch_date = today
            if member.status == "recruit":
                member.status = "active"

        # Log to rank_history
        db.add(RankHistory(
            member_id=member_id,
            old_rank=old_rank,
            new_rank=new_rank,
            changed_by=user.get("username", "unknown"),
            notes=f"Promoted via Promotions Dashboard",
            effective_date=datetime.utcnow(),
        ))

        # Sync NC rank group and display name
        if member.nc_username:
            from app.routes.member_edit import _sync_rank_group, _sync_nc_displayname
            await _sync_rank_group(member.nc_username, new_rank)
            await _sync_nc_displayname(member.nc_username, member.display_name)

        await db.commit()

        log.info(f"Promoted {member.first_name} {member.last_name} from {old_rank} → {new_rank} by {user.get('username')}")

    new_abbr = RANK_ABBR.get(new_rank, new_rank)
    old_abbr = RANK_ABBR.get(old_rank, old_rank)

    return HTMLResponse(
        content=f'<div id="promo-toast" class="toast success">✅ {old_abbr} {member.last_name} promoted to {new_abbr}</div>',
        headers={"HX-Trigger": "promotionDone"},
    )


@router.post("/api/s1/promotions/batch-promote", response_class=HTMLResponse)
@require_auth
async def batch_promote(request: Request):
    """Execute multiple promotions at once."""
    user = get_current_user(request)
    if not _has_access(user):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    form = await request.form()
    # Expect pairs: member_ids[] and new_ranks[] (parallel arrays)
    raw_pairs = form.get("promotions", "").strip()
    if not raw_pairs:
        return HTMLResponse(
            '<div class="toast error">❌ No promotions selected.</div>'
        )

    import json
    try:
        pairs = json.loads(raw_pairs)
    except Exception:
        return HTMLResponse('<div class="toast error">❌ Invalid data.</div>')

    results = []
    errors = []
    today = date.today()
    username = user.get("username", "unknown")

    async with async_session() as db:
        for p in pairs:
            mid = int(p.get("member_id", 0))
            new_rank = p.get("new_rank", "").strip()
            if not mid or not new_rank or new_rank not in RANK_INDEX:
                errors.append(f"Invalid entry: member {mid}")
                continue

            result = await db.execute(select(Member).where(Member.id == mid))
            member = result.scalar_one_or_none()
            if not member:
                errors.append(f"Member {mid} not found")
                continue

            old_rank = member.rank_grade
            old_idx = RANK_INDEX.get(old_rank, -1)
            new_idx = RANK_INDEX.get(new_rank, -1)
            if new_idx <= old_idx:
                errors.append(f"{member.last_name}: {old_rank} → {new_rank} not valid")
                continue

            if member.non_promotable_until and member.non_promotable_until >= today:
                errors.append(f"{member.last_name}: non-promotable hold")
                continue

            member.rank_grade = new_rank

            # Auto-patch: E-1 → E-2+ sets patch_date and status
            if old_rank == "E-1" and new_rank != "E-1":
                if not member.patch_date:
                    member.patch_date = today
                if member.status == "recruit":
                    member.status = "active"

            db.add(RankHistory(
                member_id=mid,
                old_rank=old_rank,
                new_rank=new_rank,
                changed_by=username,
                notes="Promoted via Batch Promotions",
                effective_date=datetime.utcnow(),
            ))

            if member.nc_username:
                from app.routes.member_edit import _sync_rank_group, _sync_nc_displayname
                await _sync_rank_group(member.nc_username, new_rank)
                await _sync_nc_displayname(member.nc_username, member.display_name)

            results.append(f"{RANK_ABBR.get(old_rank, old_rank)} {member.last_name} → {RANK_ABBR.get(new_rank, new_rank)}")
            log.info(f"Batch promoted {member.first_name} {member.last_name} from {old_rank} → {new_rank} by {username}")

        await db.commit()

    parts = []
    if results:
        parts.append(f"✅ {len(results)} promotion(s): " + ", ".join(results))
    if errors:
        parts.append(f"⚠️ {len(errors)} error(s): " + ", ".join(errors))

    return HTMLResponse(
        content=f'<div id="promo-toast" class="toast success">{"<br>".join(parts)}</div>',
        headers={"HX-Trigger": "promotionDone"},
    )