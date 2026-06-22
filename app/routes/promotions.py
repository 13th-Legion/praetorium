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
from app.models.promotion_stage import PromotionStage, OFFICER_GRADES

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


def _allowed_rank_changes(current_rank: str) -> list[tuple[str, str]]:
    """Return list of (grade, display_label) the member could be changed to (all ranks except current)."""
    choices = []
    for r in RANK_ORDER:
        if r == current_rank:
            continue
        abbr = RANK_ABBR.get(r, r)
        title = RANK_TITLE.get(r, "")
        choices.append((r, f"{abbr} — {title}"))
    return choices


def _has_access(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & (COMMAND_ROLES | S1_ROLES))


def _derive_action_type(from_rank: Optional[str], to_rank: str) -> str:
    """Classify a staged rank change.

    - 'patch'     : recruit graduation (E-1 -> anything but E-1)
    - 'demotion'  : target ranks below current
    - 'promotion' : everything else (incl. enlisted-rank-up and officer commission)
    """
    if from_rank == "E-1" and to_rank != "E-1":
        return "patch"
    from_idx = RANK_INDEX.get(from_rank, -1)
    to_idx = RANK_INDEX.get(to_rank, -1)
    if to_idx < from_idx:
        return "demotion"
    return "promotion"


def _action_label(action_type: str) -> str:
    return {"promotion": "Promotion", "patch": "Patch", "demotion": "Demotion"}.get(
        action_type, action_type.title()
    )


def _parse_target_date(raw: Optional[str]) -> Optional[date]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


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

        # Active staged rows (planned for an upcoming formation)
        stage_result = await db.execute(
            select(PromotionStage).where(PromotionStage.status == "staged")
        )
        staged_objs = stage_result.scalars().all()
        member_lookup = {m.id: m for m in members}

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
            "allowed_ranks": _allowed_rank_changes(m.rank_grade),
        })

    # Default sort: highest TIG first
    rows.sort(key=lambda r: (-r["tig_days"], r["rank_index"], r["last_name"]))

    # Build staged-panel rows (member may have left active/recruit set — resolve lazily)
    staged_member_ids = {s.member_id for s in staged_objs}
    missing_ids = staged_member_ids - set(member_lookup.keys())
    if missing_ids:
        async with async_session() as db2:
            extra = await db2.execute(
                select(Member).where(Member.id.in_(missing_ids))
            )
            for m in extra.scalars().all():
                member_lookup[m.id] = m

    staged_rows = []
    for s in staged_objs:
        m = member_lookup.get(s.member_id)
        staged_rows.append({
            "id": s.id,
            "member_id": s.member_id,
            "last_name": (m.last_name if m else "") or "",
            "first_name": (m.first_name if m else "") or "",
            "callsign": (m.callsign if m else None) or "—",
            "from_rank": s.from_rank,
            "from_abbr": RANK_ABBR.get(s.from_rank, s.from_rank or "—"),
            "to_rank": s.to_rank,
            "to_abbr": RANK_ABBR.get(s.to_rank, s.to_rank),
            "action_type": s.action_type,
            "action_label": _action_label(s.action_type),
            "is_officer": s.is_officer,
            "target_date": s.target_date,
            "target_date_str": s.target_date.strftime("%Y-%m-%d") if s.target_date else "—",
            "notes": s.notes or "",
            "allowed_ranks": _allowed_rank_changes(s.from_rank or ""),
        })
    staged_rows.sort(key=lambda r: (r["last_name"], r["first_name"]))

    return templates.TemplateResponse("pages/promotions.html", {
        "request": request,
        "user": user,
        "rows": rows,
        "staged_rows": staged_rows,
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

        today = date.today()
        old_idx = RANK_INDEX.get(old_rank, -1)
        new_idx = RANK_INDEX.get(new_rank, -1)
        is_promotion = new_idx > old_idx
        action_word = "Promoted" if is_promotion else "Demoted"

        # Check non-promotable hold (only blocks promotions, not demotions)
        if is_promotion and member.non_promotable_until and member.non_promotable_until >= today:
            raise HTTPException(400, f"Member is non-promotable until {member.non_promotable_until}")

        # Execute rank change
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
            notes=f"{action_word} via Promotions Dashboard",
            effective_date=datetime.utcnow(),
        ))

        # Sync NC rank group and display name
        if member.nc_username:
            from app.routes.member_edit import _sync_rank_group, _sync_nc_displayname
            await _sync_rank_group(member.nc_username, new_rank)
            await _sync_nc_displayname(member.nc_username, member.display_name)

        await db.commit()

        log.info(f"{action_word} {member.first_name} {member.last_name} from {old_rank} → {new_rank} by {user.get('username')}")

    new_abbr = RANK_ABBR.get(new_rank, new_rank)
    old_abbr = RANK_ABBR.get(old_rank, old_rank)
    action_label = "promoted" if RANK_INDEX.get(new_rank, -1) > RANK_INDEX.get(old_rank, -1) else "changed"

    return HTMLResponse(
        content=f'<div id="promo-toast" class="toast success">✅ {old_abbr} {member.last_name} {action_label} to {new_abbr}</div>',
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
            is_promo = new_idx > old_idx

            if is_promo and member.non_promotable_until and member.non_promotable_until >= today:
                errors.append(f"{member.last_name}: non-promotable hold")
                continue

            member.rank_grade = new_rank

            # Auto-patch: E-1 → E-2+ sets patch_date and status
            if old_rank == "E-1" and new_rank != "E-1":
                if not member.patch_date:
                    member.patch_date = today
                if member.status == "recruit":
                    member.status = "active"

            action_word = "Promoted" if is_promo else "Demoted"
            db.add(RankHistory(
                member_id=mid,
                old_rank=old_rank,
                new_rank=new_rank,
                changed_by=username,
                notes=f"{action_word} via Batch Rank Change",
                effective_date=datetime.utcnow(),
            ))

            if member.nc_username:
                from app.routes.member_edit import _sync_rank_group, _sync_nc_displayname
                await _sync_rank_group(member.nc_username, new_rank)
                await _sync_nc_displayname(member.nc_username, member.display_name)

            results.append(f"{RANK_ABBR.get(old_rank, old_rank)} {member.last_name} → {RANK_ABBR.get(new_rank, new_rank)}")
            log.info(f"Batch {action_word.lower()} {member.first_name} {member.last_name} from {old_rank} → {new_rank} by {username}")

        await db.commit()

    parts = []
    if results:
        parts.append(f"✅ {len(results)} rank change(s): " + ", ".join(results))
    if errors:
        parts.append(f"⚠️ {len(errors)} error(s): " + ", ".join(errors))

    return HTMLResponse(
        content=f'<div id="promo-toast" class="toast success">{"<br>".join(parts)}</div>',
        headers={"HX-Trigger": "promotionDone"},
    )

# ─── Staged Promotions / Patching (FTX formation workflow) ───────────────────


async def _execute_promote(db, member: Member, new_rank: str, username: str, notes: str):
    """Execute the real rank change — mirrors promote_member() exactly.

    Sets rank_grade, auto-patches recruits (E-1 -> non-E-1), logs RankHistory,
    and syncs NC rank group + display name. Caller owns the db session/commit.
    """
    old_rank = member.rank_grade
    today = date.today()

    member.rank_grade = new_rank

    # Auto-patch: E-1 -> E-2+ sets patch_date and status
    if old_rank == "E-1" and new_rank != "E-1":
        if not member.patch_date:
            member.patch_date = today
        if member.status == "recruit":
            member.status = "active"

    db.add(RankHistory(
        member_id=member.id,
        old_rank=old_rank,
        new_rank=new_rank,
        changed_by=username,
        notes=notes,
        effective_date=datetime.utcnow(),
    ))

    if member.nc_username:
        from app.routes.member_edit import _sync_rank_group, _sync_nc_displayname
        await _sync_rank_group(member.nc_username, new_rank)
        await _sync_nc_displayname(member.nc_username, member.display_name)


@router.post("/api/s1/promotions/stage", response_class=HTMLResponse)
@require_auth
async def stage_promotion(request: Request):
    """Stage a planned rank change for an upcoming FTX/formation."""
    user = get_current_user(request)
    if not _has_access(user):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    form = await request.form()
    member_id = int(form.get("member_id", 0) or 0)
    to_rank = form.get("to_rank", "").strip()
    notes = (form.get("notes", "") or "").strip() or None
    target_date = _parse_target_date(form.get("target_date"))

    if not member_id or not to_rank:
        return HTMLResponse('<div class="toast error">❌ Missing member or target rank.</div>')
    if to_rank not in RANK_INDEX:
        return HTMLResponse(f'<div class="toast error">❌ Invalid rank: {to_rank}</div>')

    today = date.today()
    username = user.get("username", "unknown")

    async with async_session() as db:
        result = await db.execute(select(Member).where(Member.id == member_id))
        member = result.scalar_one_or_none()
        if not member:
            return HTMLResponse('<div class="toast error">❌ Member not found.</div>')

        from_rank = member.rank_grade
        if to_rank == from_rank:
            return HTMLResponse('<div class="toast error">❌ Target rank matches current rank.</div>')

        action_type = _derive_action_type(from_rank, to_rank)
        is_officer = to_rank in OFFICER_GRADES

        # Block staging promotions for members on a non-promotable hold
        if action_type in ("promotion", "patch") and member.non_promotable_until and member.non_promotable_until >= today:
            return HTMLResponse(
                f'<div class="toast error">❌ {member.last_name} is non-promotable until {member.non_promotable_until}.</div>'
            )

        # Update existing active staged row instead of duplicating
        existing_q = await db.execute(
            select(PromotionStage).where(
                PromotionStage.member_id == member_id,
                PromotionStage.status == "staged",
            )
        )
        stage = existing_q.scalar_one_or_none()
        if stage:
            stage.from_rank = from_rank
            stage.to_rank = to_rank
            stage.action_type = action_type
            stage.is_officer = is_officer
            stage.target_date = target_date
            stage.notes = notes
            stage.staged_by = username
            stage.staged_at = datetime.utcnow()
            verb = "updated"
        else:
            db.add(PromotionStage(
                member_id=member_id,
                from_rank=from_rank,
                to_rank=to_rank,
                action_type=action_type,
                is_officer=is_officer,
                status="staged",
                target_date=target_date,
                notes=notes,
                staged_by=username,
                staged_at=datetime.utcnow(),
            ))
            verb = "staged"

        await db.commit()

    to_abbr = RANK_ABBR.get(to_rank, to_rank)
    from_abbr = RANK_ABBR.get(from_rank, from_rank or "—")
    label = _action_label(action_type)
    return HTMLResponse(
        content=f'<div id="promo-toast" class="toast success">📋 {label} {verb}: {from_abbr} {member.last_name} → {to_abbr}</div>',
        headers={"HX-Trigger": "promotionStaged"},
    )


@router.post("/api/s1/promotions/stage/{stage_id}/remove", response_class=HTMLResponse)
@require_auth
async def remove_stage(request: Request, stage_id: int):
    """Cancel a staged row (the no-show / scrub case)."""
    user = get_current_user(request)
    if not _has_access(user):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    async with async_session() as db:
        result = await db.execute(select(PromotionStage).where(PromotionStage.id == stage_id))
        stage = result.scalar_one_or_none()
        if not stage:
            return HTMLResponse('<div class="toast error">❌ Staged entry not found.</div>')
        if stage.status != "staged":
            return HTMLResponse('<div class="toast error">❌ Entry already finalized/cancelled.</div>')
        stage.status = "cancelled"
        await db.commit()

    return HTMLResponse(
        content='<div id="promo-toast" class="toast success">🗑️ Removed from formation.</div>',
        headers={"HX-Trigger": "promotionStaged"},
    )


@router.post("/api/s1/promotions/stage/{stage_id}/edit", response_class=HTMLResponse)
@require_auth
async def edit_stage(request: Request, stage_id: int):
    """Change the target rank on a staged row (recomputes action_type/is_officer)."""
    user = get_current_user(request)
    if not _has_access(user):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    form = await request.form()
    to_rank = form.get("to_rank", "").strip()
    if not to_rank or to_rank not in RANK_INDEX:
        return HTMLResponse(f'<div class="toast error">❌ Invalid rank: {to_rank}</div>')

    async with async_session() as db:
        result = await db.execute(select(PromotionStage).where(PromotionStage.id == stage_id))
        stage = result.scalar_one_or_none()
        if not stage:
            return HTMLResponse('<div class="toast error">❌ Staged entry not found.</div>')
        if stage.status != "staged":
            return HTMLResponse('<div class="toast error">❌ Entry already finalized/cancelled.</div>')

        stage.to_rank = to_rank
        stage.action_type = _derive_action_type(stage.from_rank, to_rank)
        stage.is_officer = to_rank in OFFICER_GRADES
        stage.staged_by = user.get("username", "unknown")
        stage.staged_at = datetime.utcnow()
        await db.commit()

    to_abbr = RANK_ABBR.get(to_rank, to_rank)
    return HTMLResponse(
        content=f'<div id="promo-toast" class="toast success">✏️ Updated target → {to_abbr}</div>',
        headers={"HX-Trigger": "promotionStaged"},
    )


@router.post("/api/s1/promotions/finalize", response_class=HTMLResponse)
@require_auth
async def finalize_staged(request: Request):
    """Execute all status='staged' rows (optionally filtered by target_date).

    Runs the exact promote logic (rank_grade, auto-patch, RankHistory, NC sync)
    for each, marks the stage row finalized. Skips + reports non-promotable or
    missing members.
    """
    user = get_current_user(request)
    if not _has_access(user):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    form = await request.form()
    target_date = _parse_target_date(form.get("target_date"))

    today = date.today()
    username = user.get("username", "unknown")
    successes = []
    errors = []

    async with async_session() as db:
        q = select(PromotionStage).where(PromotionStage.status == "staged")
        if target_date is not None:
            q = q.where(PromotionStage.target_date == target_date)
        result = await db.execute(q)
        stages = result.scalars().all()

        if not stages:
            return HTMLResponse('<div class="toast error">❌ No staged rank changes to finalize.</div>')

        for stage in stages:
            m_result = await db.execute(select(Member).where(Member.id == stage.member_id))
            member = m_result.scalar_one_or_none()
            if not member:
                errors.append(f"member #{stage.member_id} missing")
                continue

            # Re-check non-promotable hold at finalize time (only for promotions/patches)
            if stage.action_type in ("promotion", "patch") and member.non_promotable_until and member.non_promotable_until >= today:
                errors.append(f"{member.last_name}: non-promotable hold")
                continue

            try:
                await _execute_promote(
                    db, member, stage.to_rank, username,
                    notes=f"{_action_label(stage.action_type)} finalized at formation",
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("Finalize failed for stage %s", stage.id)
                errors.append(f"{member.last_name}: {exc}")
                continue

            stage.status = "finalized"
            stage.finalized_by = username
            stage.finalized_at = datetime.utcnow()
            successes.append(
                f"{RANK_ABBR.get(stage.from_rank, stage.from_rank or '—')} {member.last_name} → {RANK_ABBR.get(stage.to_rank, stage.to_rank)}"
            )
            log.info("Finalized stage %s: %s %s -> %s by %s",
                     stage.id, member.last_name, stage.from_rank, stage.to_rank, username)

        await db.commit()

    parts = []
    if successes:
        parts.append(f"✅ {len(successes)} finalized: " + ", ".join(successes))
    if errors:
        parts.append(f"⚠️ {len(errors)} skipped: " + ", ".join(errors))

    return HTMLResponse(
        content=f'<div id="promo-toast" class="toast success">{"<br>".join(parts)}</div>',
        headers={"HX-Trigger": "promotionDone"},
    )


@router.get("/api/s1/promotions/export", response_class=HTMLResponse)
@require_auth
async def export_formation(request: Request):
    """Print-friendly formation sheet of all staged rows + conditional oath text."""
    user = get_current_user(request)
    if not _has_access(user):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    async with async_session() as db:
        result = await db.execute(
            select(PromotionStage).where(PromotionStage.status == "staged")
        )
        stages = result.scalars().all()

        member_ids = {s.member_id for s in stages}
        members_by_id = {}
        if member_ids:
            m_result = await db.execute(select(Member).where(Member.id.in_(member_ids)))
            members_by_id = {m.id: m for m in m_result.scalars().all()}

    formation_dates = sorted({s.target_date for s in stages if s.target_date})
    if len(formation_dates) == 1:
        formation_date_str = formation_dates[0].strftime("%A, %B %d, %Y")
    elif formation_dates:
        formation_date_str = ", ".join(d.strftime("%Y-%m-%d") for d in formation_dates)
    else:
        formation_date_str = "TBD"

    rows = []
    show_enlisted_oath = False
    show_officer_oath = False
    for s in stages:
        m = members_by_id.get(s.member_id)
        rows.append({
            "last_name": (m.last_name if m else "") or "",
            "first_name": (m.first_name if m else "") or "",
            "callsign": (m.callsign if m else None) or "—",
            "from_abbr": RANK_ABBR.get(s.from_rank, s.from_rank or "—"),
            "to_abbr": RANK_ABBR.get(s.to_rank, s.to_rank),
            "to_title": RANK_TITLE.get(s.to_rank, ""),
            "action_label": _action_label(s.action_type),
            "action_type": s.action_type,
            "is_officer": s.is_officer,
            "notes": s.notes or "",
        })
        if s.is_officer:
            show_officer_oath = True
        # Enlisted oath: any patch OR any enlisted (E-*/W-*) promotion
        if s.action_type == "patch":
            show_enlisted_oath = True
        elif (not s.is_officer) and (s.to_rank or "").split("-")[0] in ("E", "W"):
            show_enlisted_oath = True

    rows.sort(key=lambda r: (r["last_name"], r["first_name"]))

    return templates.TemplateResponse("pages/promotion_formation.html", {
        "request": request,
        "user": user,
        "rows": rows,
        "formation_date_str": formation_date_str,
        "show_enlisted_oath": show_enlisted_oath,
        "show_officer_oath": show_officer_oath,
    })
