"""S2 Intelligence & Security dashboard — IIR, challenge/password, training sites.

Four areas (see `projects/s2-dashboard.md`):

  * /api/s2/iir          — IIR (Intelligence Information Report) list + authoring
  * /api/s2/challenge    — challenge / password / running-password rotation
  * /api/s2/sites        — training-site map library (add/remove sites, upload maps)
  * /api/s2              — hub

RBAC:
  * View/manage: S2 + Command + admin (`S2_ROLES`).
  * IIR dissemination tier controls who SEES it (unit-wide vs command-only),
    but only S2/Command/admin can AUTHOR regardless of tier.

IIR body is authored in the shared Quill editor (rich HTML); sanitized with
bleach on save, same as announcements/newsletter. On publish, unit-wide IIRs
fan out a bell notification to everyone; command-only IIRs notify S2/Command.

Training sites are DB-backed (S2TrainingSite/S2TrainingSiteMap), replacing the
hardcoded `app/training_sites.py` dict; the FTX builder reads from this same
source via `app/services/training_sites.py`.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import bleach
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.auth import get_current_user, require_auth
from app.database import get_db
from app.models.events import Event
from app.models.member import Member
from app.models.s2_intel import (
    IIR,
    S2ChallengePassword,
    S2TrainingSite,
    S2TrainingSiteMap,
)
from app.services import training_sites as _ts

log = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/api/s2", tags=["s2-admin"])

S2_ROLES = {"s2", "command", "admin"}

# Bleach allowlist mirrors the announcement/newsletter editors (Quill HTML).
_ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p", "br", "strong", "em", "u", "s", "ol", "ul", "li", "h1", "h2", "h3",
    "blockquote", "a", "img", "pre", "code", "span",
}
_ALLOWED_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "width", "height"],
    "span": ["class"],
}


def _can_manage(user: dict) -> bool:
    return bool(user and set(user.get("roles", [])) & S2_ROLES)


def _denied() -> Response:
    return HTMLResponse("<h2>Access Denied</h2>", status_code=403)


async def _current_member(request: Request, db: AsyncSession) -> Member | None:
    user = get_current_user(request)
    if not user:
        return None
    uname = user.get("username")
    res = await db.execute(select(Member).where(Member.nc_username == uname))
    return res.scalar_one_or_none()


def _display_name(m: Member) -> str:
    from app.services import ranks as _ranks
    abbr = _ranks.abbr_map().get(m.rank_grade, m.rank_grade or "")
    base = f"{abbr} {m.last_name}".strip()
    if m.callsign:
        base += f" ({m.callsign})"
    return base


def _clean_html(raw: str) -> str:
    return bleach.clean(raw or "", tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)


def _dtg(dt: datetime) -> str:
    """Military-ish DTG string, e.g. '241800Z SEP 26'."""
    return dt.strftime("%d%H%MZ %b %y").upper()


# ─────────────────────────────────────────────────────────────────────────────
# Hub
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@require_auth
async def s2_hub(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()

    active_iirs = (await db.execute(
        select(func.count(IIR.id)).where(IIR.status == "active")
    )).scalar_one()
    active_cp = (await db.execute(
        select(func.count(S2ChallengePassword.id)).where(S2ChallengePassword.active.is_(True))
    )).scalar_one()
    sites = await _ts.all_sites()

    return templates.TemplateResponse("pages/s2_hub.html", {
        "request": request,
        "user": user,
        "active_iirs": active_iirs,
        "active_cp": active_cp,
        "site_count": len(sites),
    })


# ─────────────────────────────────────────────────────────────────────────────
# IIR — Intelligence Information Report
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/iir", response_class=HTMLResponse)
@require_auth
async def iir_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    is_s2 = _can_manage(user)
    member = await _current_member(request, db)

    iirs = (await db.execute(
        select(IIR).order_by(desc(IIR.created_at))
    )).scalars().all()
    # Non-S2/Command only see unit-wide active IIRs.
    visible = []
    for i in iirs:
        if is_s2 or (i.dissemination_tier == "unit_wide" and i.status == "active"):
            visible.append(i)

    ids = {i.author_id for i in visible}
    names: dict[int, str] = {}
    if ids:
        for m in (await db.execute(select(Member).where(Member.id.in_(ids)))).scalars().all():
            names[m.id] = _display_name(m)

    return templates.TemplateResponse("pages/s2_iir_list.html", {
        "request": request,
        "user": user,
        "is_s2": is_s2,
        "iirs": visible,
        "names": names,
    })


@router.get("/iir/new", response_class=HTMLResponse)
@require_auth
async def iir_new(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()
    return templates.TemplateResponse("pages/s2_iir_edit.html", {
        "request": request, "user": user, "iir": None,
    })


@router.get("/iir/{iir_id}/edit", response_class=HTMLResponse)
@require_auth
async def iir_edit(request: Request, iir_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()
    iir = (await db.execute(select(IIR).where(IIR.id == iir_id))).scalar_one_or_none()
    if not iir:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    return templates.TemplateResponse("pages/s2_iir_edit.html", {
        "request": request, "user": user, "iir": iir,
    })


@router.post("/iir/save")
@require_auth
async def iir_save(request: Request, db: AsyncSession = Depends(get_db)):
    """Create or update an IIR. On create, fan out the bell notification."""
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()
    member = await _current_member(request, db)

    form = await request.form()
    iir_id = (form.get("iir_id") or "").strip()
    subject = (form.get("subject") or "").strip()
    country_area = (form.get("country_area") or "").strip()
    tier = (form.get("dissemination_tier") or "command_only").strip()
    details = _clean_html(form.get("details") or "")

    def _err(msg: str):
        return HTMLResponse(f'<div class="s2-flash s2-err">{msg}</div>', status_code=400)

    if not subject or not country_area or not details.strip():
        return _err("Subject, area, and details are required.")
    if tier not in ("unit_wide", "command_only"):
        tier = "command_only"

    is_new = False
    if iir_id.isdigit():
        iir = (await db.execute(select(IIR).where(IIR.id == int(iir_id)))).scalar_one_or_none()
        if not iir:
            return _err("IIR not found.")
    else:
        iir = IIR(dtg=_dtg(datetime.now(timezone.utc)))
        is_new = True

    iir.subject = subject
    iir.country_area = country_area
    iir.source = (form.get("source") or "").strip() or None
    iir.reliability_rating = (form.get("reliability_rating") or "").strip() or None
    iir.credibility = (form.get("credibility") or "").strip() or None
    iir.details = details
    iir.assessment = _clean_html(form.get("assessment") or "") or None
    iir.remarks = (form.get("remarks") or "").strip() or None
    iir.dissemination_tier = tier
    if is_new:
        iir.author_id = member.id if member else None
        db.add(iir)
        await db.flush()
        iir.report_number = f"IIR-{iir.id:04d}"

    await db.commit()
    await db.refresh(iir)

    # Fan out bell notifications on create.
    if is_new:
        from app.routes.notifications import create_notification_for_all, create_notification_for_roles
        title = f"📡 IIR {iir.report_number}: {iir.subject}"
        body = f"{iir.country_area} — {('unit-wide' if tier == 'unit_wide' else 'S2/Command')} intel report."
        link = "/api/s2/iir"
        if tier == "unit_wide":
            await create_notification_for_all(db, "intel", title, body=body, link=link, icon="📡")
        else:
            await create_notification_for_roles(db, ["s2", "command", "admin"], "intel", title, body=body, link=link, icon="📡")

    return RedirectResponse(url="/api/s2/iir", status_code=302)


@router.post("/iir/{iir_id}/archive")
@require_auth
async def iir_archive(request: Request, iir_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()
    iir = (await db.execute(select(IIR).where(IIR.id == iir_id))).scalar_one_or_none()
    if not iir:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    iir.status = "archived"
    iir.archived_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url="/api/s2/iir", status_code=302)


@router.post("/iir/{iir_id}/delete")
@require_auth
async def iir_delete(request: Request, iir_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()
    iir = (await db.execute(select(IIR).where(IIR.id == iir_id))).scalar_one_or_none()
    if not iir:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    await db.delete(iir)
    await db.commit()
    return RedirectResponse(url="/api/s2/iir", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# Challenge / Password / Running Password
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/challenge", response_class=HTMLResponse)
@require_auth
async def challenge_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()

    sets = (await db.execute(
        select(S2ChallengePassword).order_by(desc(S2ChallengePassword.created_at))
    )).scalars().all()
    events = (await db.execute(
        select(Event).order_by(desc(Event.date_start)).limit(30)
    )).scalars().all()

    return templates.TemplateResponse("pages/s2_challenge.html", {
        "request": request, "user": user, "sets": sets, "events": events,
    })


@router.post("/challenge/save")
@require_auth
async def challenge_save(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()

    form = await request.form()
    challenge = (form.get("challenge") or "").strip()
    password = (form.get("password") or "").strip()

    def _err(msg: str):
        return HTMLResponse(f'<div class="s2-flash s2-err">{msg}</div>', status_code=400)

    if not challenge or not password:
        return _err("Challenge and password are required.")

    s = S2ChallengePassword(
        label=(form.get("label") or "").strip() or None,
        event_id=int(form["event_id"]) if (form.get("event_id") or "").isdigit() else None,
        challenge=challenge,
        password=password,
        running_password=(form.get("running_password") or "").strip() or None,
        created_by=user.get("username"),
    )
    vf = (form.get("valid_from") or "").strip()
    vu = (form.get("valid_until") or "").strip()
    if vf:
        try:
            s.valid_from = datetime.fromisoformat(vf)
        except ValueError:
            pass
    if vu:
        try:
            s.valid_until = datetime.fromisoformat(vu)
        except ValueError:
            pass

    db.add(s)
    await db.commit()
    return RedirectResponse(url="/api/s2/challenge", status_code=302)


@router.post("/challenge/{cp_id}/deactivate")
@require_auth
async def challenge_deactivate(request: Request, cp_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()
    s = (await db.execute(select(S2ChallengePassword).where(S2ChallengePassword.id == cp_id))).scalar_one_or_none()
    if not s:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    s.active = False
    await db.commit()
    return RedirectResponse(url="/api/s2/challenge", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# Training sites + maps
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sites", response_class=HTMLResponse)
@require_auth
async def sites_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()

    sites = (await db.execute(
        select(S2TrainingSite).order_by(S2TrainingSite.id)
    )).scalars().all()
    # Attach maps per site.
    site_ids = [s.id for s in sites]
    maps_by_site: dict[int, list[S2TrainingSiteMap]] = {}
    if site_ids:
        for m in (await db.execute(
            select(S2TrainingSiteMap).where(S2TrainingSiteMap.site_id.in_(site_ids)).order_by(S2TrainingSiteMap.id)
        )).scalars().all():
            maps_by_site.setdefault(m.site_id, []).append(m)

    return templates.TemplateResponse("pages/s2_sites.html", {
        "request": request, "user": user, "sites": sites, "maps_by_site": maps_by_site,
    })


@router.post("/sites/save")
@require_auth
async def site_save(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()

    form = await request.form()
    site_id = (form.get("site_id") or "").strip()
    name = (form.get("name") or "").strip()
    key = (form.get("key") or "").strip().lower()

    def _err(msg: str):
        return HTMLResponse(f'<div class="s2-flash s2-err">{msg}</div>', status_code=400)

    if not name:
        return _err("Site name is required.")

    if site_id.isdigit():
        site = (await db.execute(select(S2TrainingSite).where(S2TrainingSite.id == int(site_id)))).scalar_one_or_none()
        if not site:
            return _err("Site not found.")
    else:
        if not key or not re.fullmatch(r"[a-z0-9_-]+", key):
            return _err("A unique slug (lowercase letters/numbers/dashes) is required for a new site.")
        dup = (await db.execute(select(S2TrainingSite).where(S2TrainingSite.key == key))).scalar_one_or_none()
        if dup:
            return _err("That slug is already in use.")
        site = S2TrainingSite(key=key, name=name)
        db.add(site)

    site.name = name
    site.nickname = (form.get("nickname") or "").strip() or None
    site.address = (form.get("address") or "").strip() or None

    await db.commit()
    _ts.invalidate()
    return RedirectResponse(url="/api/s2/sites", status_code=302)


@router.post("/sites/{site_id}/deactivate")
@require_auth
async def site_deactivate(request: Request, site_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()
    site = (await db.execute(select(S2TrainingSite).where(S2TrainingSite.id == site_id))).scalar_one_or_none()
    if not site:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    site.is_active = False
    await db.commit()
    _ts.invalidate()
    return RedirectResponse(url="/api/s2/sites", status_code=302)


@router.post("/sites/{site_id}/maps")
@require_auth
async def site_map_upload(request: Request, site_id: int, db: AsyncSession = Depends(get_db)):
    """Upload a map (PDF/image) for a training site to Nextcloud."""
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()
    site = (await db.execute(select(S2TrainingSite).where(S2TrainingSite.id == site_id))).scalar_one_or_none()
    if not site:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)

    form = await request.form()
    label = (form.get("label") or "").strip()
    file = form.get("map_file")

    def _err(msg: str):
        return HTMLResponse(f'<div class="s2-flash s2-err">{msg}</div>', status_code=400)

    if not label:
        return _err("Map label is required.")
    if not isinstance(file, UploadFile) or not getattr(file, "filename", None):
        return _err("Select a map file to upload.")

    data = await file.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        return _err("Map exceeds the 16MB limit.")

    url = await _store_map(site.key, file.filename or "map", label, data)
    if not url:
        return _err("Could not store the map in Nextcloud.")

    db.add(S2TrainingSiteMap(site_id=site.id, label=label, url=url))
    await db.commit()
    _ts.invalidate()
    return RedirectResponse(url="/api/s2/sites", status_code=302)


@router.post("/sites/maps/{map_id}/delete")
@require_auth
async def site_map_delete(request: Request, map_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_manage(user):
        return _denied()
    m = (await db.execute(select(S2TrainingSiteMap).where(S2TrainingSiteMap.id == map_id))).scalar_one_or_none()
    if not m:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    await db.delete(m)
    await db.commit()
    _ts.invalidate()
    return RedirectResponse(url="/api/s2/sites", status_code=302)


async def _store_map(site_key: str, filename: str, label: str, data: bytes) -> str | None:
    """Upload a map to NC S2 folder; returns the WebDAV path or None."""
    import httpx
    from app.settings import NC_SVC_PASS, NC_SVC_USER
    from config import get_settings
    settings = get_settings()
    base = "/remote.php/dav/files/spooky/13th%20Legion%20Shared/%5bS-2%5d%20Intel-Security/Maps"
    safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in site_key)
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    safe_fn = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename).strip() or "map"
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = f"{base}/{safe_key}/{safe_label}_{date_str}_{safe_fn}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            folder = f"{base}/{safe_key}"
            await client.request("MKCOL", f"{settings.nc_url}{folder}/", auth=(NC_SVC_USER, NC_SVC_PASS))
            resp = await client.put(f"{settings.nc_url}{path}", content=data, auth=(NC_SVC_USER, NC_SVC_PASS))
            if resp.status_code in (201, 204):
                return path
            log.warning("S2 map upload returned %s for %s/%s", resp.status_code, site_key, label)
            return None
    except Exception:
        log.exception("S2 map upload failed for %s/%s", site_key, label)
        return None
