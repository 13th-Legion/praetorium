"""Legionary Dispatch — unit newsletter routes (S1).

Extends Unit Comms with full newsletters: hosted inline images, file
attachments, seasonal crest mastheads, send-now + scheduled delivery, and a
self-building Nextcloud archive.
"""
from __future__ import annotations

import os
import uuid
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

import bleach

from app.auth import require_auth
from app.database import get_db
from app.constants import UNIT_COMMS_ROLES
from app.models.newsletter import Newsletter, NewsletterImage, NewsletterAttachment
from app.models.newsletter_section import NewsletterSectionTemplate
from app.models.events import Event
from app.newsletter_assets import (
    SEASONAL_CRESTS, DEFAULT_CREST, crest_url, crest_available,
    NEWSLETTER_IMG_DIR, NEWSLETTER_ATTACH_DIR, image_url,
    MAX_IMAGE_BYTES, MAX_ATTACH_BYTES, MAX_TOTAL_ATTACH_BYTES,
    ALLOWED_IMAGE_MIMES, ALLOWED_ATTACH_MIMES,
)
from app.newsletter_send import EMAIL_BLAST_GROUPS, resolve_recipients
from app.newsletter_scheduler import deliver_newsletter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/s1/newsletter", tags=["s1-newsletter"])
templates = Jinja2Templates(directory="app/templates")

_CDT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")

# Allowed HTML from the Quill editor (superset of announcement sanitization,
# plus <img> with our hosted URLs).
ALLOWED_TAGS = [
    "p", "br", "strong", "em", "u", "s", "h1", "h2", "h3",
    "ul", "ol", "li", "blockquote", "a", "img", "span", "div",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "width", "height", "style"],
    "span": ["style"],
    "div": ["style"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def _user(request: Request) -> dict:
    return request.session.get("user", {})


def _require_s1(user: dict):
    roles = set(user.get("roles", []))
    if not (roles & set(UNIT_COMMS_ROLES)):
        raise HTTPException(status_code=403, detail="S1 / Command access required")


def _sanitize(html: str) -> str:
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS,
                        protocols=ALLOWED_PROTOCOLS, strip=True)


# ─── List / archive ──────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
@require_auth
async def newsletter_home(request: Request, db: AsyncSession = Depends(get_db)):
    user = _user(request)
    _require_s1(user)
    rows = (await db.execute(
        select(Newsletter).order_by(desc(Newsletter.created_at)).limit(50)
    )).scalars().all()
    return templates.TemplateResponse("pages/newsletter_list.html", {
        "request": request, "user": user, "newsletters": rows,
    })


# ─── Compose / edit ──────────────────────────────────────────────────────────
@router.get("/new", response_class=HTMLResponse)
@require_auth
async def newsletter_new(request: Request, db: AsyncSession = Depends(get_db)):
    user = _user(request)
    _require_s1(user)
    return templates.TemplateResponse("pages/newsletter_edit.html", {
        "request": request, "user": user, "nl": None,
        "groups": EMAIL_BLAST_GROUPS, "crests": SEASONAL_CRESTS,
        "default_crest": DEFAULT_CREST,
    })


@router.get("/{nl_id}/edit", response_class=HTMLResponse)
@require_auth
async def newsletter_edit(request: Request, nl_id: int, db: AsyncSession = Depends(get_db)):
    user = _user(request)
    _require_s1(user)
    nl = await db.get(Newsletter, nl_id)
    if not nl:
        raise HTTPException(404, "Newsletter not found")
    imgs = (await db.execute(
        select(NewsletterImage).where(NewsletterImage.newsletter_id == nl_id)
    )).scalars().all()
    atts = (await db.execute(
        select(NewsletterAttachment).where(NewsletterAttachment.newsletter_id == nl_id)
    )).scalars().all()
    return templates.TemplateResponse("pages/newsletter_edit.html", {
        "request": request, "user": user, "nl": nl,
        "groups": EMAIL_BLAST_GROUPS, "crests": SEASONAL_CRESTS,
        "default_crest": DEFAULT_CREST, "images": imgs, "attachments": atts,
    })


# ─── Inline image upload (returns hosted URL for the editor) ──────────────────
@router.post("/image-upload", response_model=None)
@require_auth
async def newsletter_image_upload(request: Request, db: AsyncSession = Depends(get_db)):
    user = _user(request)
    _require_s1(user)
    form = await request.form()
    file: UploadFile = form.get("file")
    if not file or not getattr(file, "filename", None):
        return JSONResponse({"error": "No file provided."}, status_code=400)
    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        return JSONResponse({"error": f"Image exceeds {MAX_IMAGE_BYTES // (1024*1024)}MB limit."}, status_code=400)
    mime = file.content_type or "image/png"
    if mime not in ALLOWED_IMAGE_MIMES:
        return JSONResponse({"error": "Unsupported image type."}, status_code=400)
    ext = os.path.splitext(file.filename or "")[1].lower() or ".png"
    stored = f"{uuid.uuid4().hex}{ext}"
    NEWSLETTER_IMG_DIR.mkdir(parents=True, exist_ok=True)
    with open(NEWSLETTER_IMG_DIR / stored, "wb") as fh:
        fh.write(data)
    rec = NewsletterImage(
        newsletter_id=None, filename=stored, orig_name=file.filename or stored,
        mime_type=mime, size=len(data),
    )
    db.add(rec)
    await db.commit()
    return JSONResponse({"url": image_url(stored)})


# ─── Attachment upload ───────────────────────────────────────────────────────
@router.post("/{nl_id}/attachment", response_class=HTMLResponse, response_model=None)
@require_auth
async def newsletter_attachment_add(request: Request, nl_id: int, db: AsyncSession = Depends(get_db)):
    user = _user(request)
    _require_s1(user)
    nl = await db.get(Newsletter, nl_id)
    if not nl:
        raise HTTPException(404, "Newsletter not found")
    form = await request.form()
    file: UploadFile = form.get("file")
    if not file or not getattr(file, "filename", None):
        return HTMLResponse('<div style="color:#ef5350;">No file provided.</div>', status_code=400)
    data = await file.read()
    mime = file.content_type or "application/octet-stream"
    if mime not in ALLOWED_ATTACH_MIMES:
        return HTMLResponse('<div style="color:#ef5350;">Unsupported file type.</div>', status_code=400)
    if len(data) > MAX_ATTACH_BYTES:
        return HTMLResponse(f'<div style="color:#ef5350;">File exceeds {MAX_ATTACH_BYTES // (1024*1024)}MB.</div>', status_code=400)

    existing = (await db.execute(
        select(NewsletterAttachment).where(NewsletterAttachment.newsletter_id == nl_id)
    )).scalars().all()
    total = sum(a.size for a in existing) + len(data)
    if total > MAX_TOTAL_ATTACH_BYTES:
        return HTMLResponse(
            f'<div style="color:#ef5350;">Total attachments would exceed {MAX_TOTAL_ATTACH_BYTES // (1024*1024)}MB (Proton limit).</div>',
            status_code=400,
        )

    ext = os.path.splitext(file.filename or "")[1].lower()
    stored = f"{uuid.uuid4().hex}{ext}"
    NEWSLETTER_ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    with open(NEWSLETTER_ATTACH_DIR / stored, "wb") as fh:
        fh.write(data)
    rec = NewsletterAttachment(
        newsletter_id=nl_id, filename=stored, orig_name=file.filename or stored,
        mime_type=mime, size=len(data),
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return _attachment_row_html(rec, nl_id)


@router.post("/{nl_id}/attachment/{att_id}/delete", response_class=HTMLResponse, response_model=None)
@require_auth
async def newsletter_attachment_delete(
    request: Request, nl_id: int, att_id: int, db: AsyncSession = Depends(get_db)
):
    user = _user(request)
    _require_s1(user)
    att = await db.get(NewsletterAttachment, att_id)
    if att and att.newsletter_id == nl_id:
        try:
            (NEWSLETTER_ATTACH_DIR / att.filename).unlink(missing_ok=True)
        except Exception:
            pass
        await db.delete(att)
        await db.commit()
    return HTMLResponse("")  # htmx removes the row


def _attachment_row_html(att: NewsletterAttachment, nl_id: int) -> str:
    kb = max(1, att.size // 1024)
    return f'''<div id="att-{att.id}" style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:rgba(255,255,255,0.04);border:1px solid #444;border-radius:4px;margin-bottom:6px;">
      <span style="font-size:13px;color:#ddd;">📎 {att.orig_name} <span style="color:#888;">({kb} KB)</span></span>
      <button type="button" hx-post="/api/s1/newsletter/{nl_id}/attachment/{att.id}/delete" hx-target="#att-{att.id}" hx-swap="outerHTML"
        style="background:none;border:none;color:#ef5350;cursor:pointer;font-size:13px;">✕</button>
    </div>'''


# ─── Save (draft / schedule) ─────────────────────────────────────────────────
@router.post("/save", response_class=JSONResponse, response_model=None)
@require_auth
async def newsletter_save(request: Request, db: AsyncSession = Depends(get_db)):
    user = _user(request)
    _require_s1(user)

    form = await request.form()
    nl_id = (form.get("nl_id") or "").strip()
    title = (form.get("title") or "").strip()
    subject = (form.get("subject") or "").strip()
    crest_key = form.get("crest_key") or DEFAULT_CREST
    body_html = form.get("body_html") or ""
    groups = form.get("groups") or ""
    action = form.get("action") or "draft"
    scheduled_date = form.get("scheduled_date") or ""
    scheduled_time = form.get("scheduled_time") or ""
    if not title:
        return JSONResponse({"error": "Title is required."}, status_code=400)
    if not subject:
        return JSONResponse({"error": "Subject is required."}, status_code=400)
    if crest_key not in SEASONAL_CRESTS:
        crest_key = DEFAULT_CREST

    clean_body = _sanitize(body_html or "")
    group_keys = [g for g in groups.split(",") if g in EMAIL_BLAST_GROUPS]

    # Load or create
    nl = None
    if nl_id:
        nl = await db.get(Newsletter, int(nl_id))
    if nl is None:
        nl = Newsletter(created_by=user.get("username", ""),
                        created_by_name=user.get("display_name", user.get("username", "S1")))
        db.add(nl)

    nl.title = title
    nl.subject = subject
    nl.crest_key = crest_key
    nl.body_html = clean_body
    nl.groups_csv = ",".join(group_keys)
    if not nl.created_by_name:
        nl.created_by_name = user.get("display_name", user.get("username", "S1"))

    if action in ("schedule", "send"):
        if not group_keys:
            return JSONResponse({"error": "Select at least one recipient group."}, status_code=400)

    if action == "schedule":
        if not scheduled_date or not scheduled_time:
            return JSONResponse({"error": "Pick a date and time to schedule."}, status_code=400)
        try:
            local = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M").replace(tzinfo=_CDT)
        except ValueError:
            return JSONResponse({"error": "Invalid date/time."}, status_code=400)
        utc = local.astimezone(_UTC).replace(tzinfo=None)
        if utc <= datetime.utcnow():
            return JSONResponse({"error": "Scheduled time must be in the future."}, status_code=400)
        nl.scheduled_at = utc
        nl.status = "scheduled"
    elif action == "draft":
        nl.status = "draft"
        nl.scheduled_at = None

    await db.commit()
    await db.refresh(nl)

    # Re-key any orphan inline images uploaded during this compose session.
    # (Images upload with newsletter_id=NULL; attach the most-recent unowned
    #  ones to this newsletter so cleanup/cascade works.)
    if nl.id:
        orphans = (await db.execute(
            select(NewsletterImage).where(NewsletterImage.newsletter_id.is_(None))
        )).scalars().all()
        for img in orphans:
            if img.filename in (nl.body_html or ""):
                img.newsletter_id = nl.id
        await db.commit()

    return JSONResponse({"id": nl.id, "status": nl.status})


# ─── Preview recipients ──────────────────────────────────────────────────────
@router.post("/preview-recipients", response_class=HTMLResponse, response_model=None)
@require_auth
async def newsletter_preview_recipients(request: Request, db: AsyncSession = Depends(get_db)):
    user = _user(request)
    _require_s1(user)
    form = await request.form()
    groups = form.get("groups") or ""
    group_keys = [g for g in groups.split(",") if g in EMAIL_BLAST_GROUPS]
    if not group_keys:
        return HTMLResponse('<div style="color:#ef5350;padding:8px;">Select at least one group.</div>')
    recipients = await resolve_recipients(db, group_keys)
    if not recipients:
        return HTMLResponse('<div style="color:#ef5350;padding:8px;">No recipients with email on file.</div>')
    names = ", ".join(f'<span style="color:#ccc;">{n}</span>' for _e, n in recipients)
    return HTMLResponse(f'''<div style="padding:12px;background:rgba(212,165,55,0.1);border:1px solid rgba(212,165,55,0.3);border-radius:6px;margin-top:8px;">
      <div style="font-weight:600;color:#d4a537;margin-bottom:6px;">📨 {len(recipients)} recipients:</div>
      <div style="font-size:12px;line-height:1.6;">{names}</div></div>''')


# ─── Send now ────────────────────────────────────────────────────────────────
@router.post("/{nl_id}/send-now", response_class=JSONResponse, response_model=None)
@require_auth
async def newsletter_send_now(
    nl_id: int, request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    user = _user(request)
    _require_s1(user)
    nl = await db.get(Newsletter, nl_id)
    if not nl:
        raise HTTPException(404, "Newsletter not found")
    if not [g for g in (nl.groups_csv or "").split(",") if g]:
        return JSONResponse({"error": "No recipient groups selected. Save with recipients first."}, status_code=400)
    if nl.status in ("sending", "sent"):
        return JSONResponse({"error": f"Already {nl.status}."}, status_code=400)
    # deliver_newsletter handles status, recipients, attachments, archive.
    background_tasks.add_task(deliver_newsletter, nl_id)
    return JSONResponse({"queued": True, "id": nl_id})


# ─── Cancel a scheduled send ─────────────────────────────────────────────────
@router.post("/{nl_id}/cancel", response_class=JSONResponse, response_model=None)
@require_auth
async def newsletter_cancel(request: Request, nl_id: int, db: AsyncSession = Depends(get_db)):
    user = _user(request)
    _require_s1(user)
    nl = await db.get(Newsletter, nl_id)
    if nl and nl.status == "scheduled":
        nl.status = "draft"
        nl.scheduled_at = None
        await db.commit()
    return JSONResponse({"ok": True})


@router.post("/{nl_id}/delete", response_class=JSONResponse, response_model=None)
@require_auth
async def newsletter_delete(request: Request, nl_id: int, db: AsyncSession = Depends(get_db)):
    user = _user(request)
    _require_s1(user)
    nl = await db.get(Newsletter, nl_id)
    if nl and nl.status != "sent":
        # remove attachment files
        atts = (await db.execute(
            select(NewsletterAttachment).where(NewsletterAttachment.newsletter_id == nl_id)
        )).scalars().all()
        for a in atts:
            try:
                (NEWSLETTER_ATTACH_DIR / a.filename).unlink(missing_ok=True)
            except Exception:
                pass
        await db.delete(nl)
        await db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Cannot delete a sent newsletter."}, status_code=400)


# ─── Section template library (one-click section blocks) ─────────────────────

async def _render_dynamic_section(db: AsyncSession, source: str, fallback_html: str) -> str:
    """Render a dynamic section's body at compose time. Currently supports the
    live events/training calendar pulled from Praetorium events."""
    if source == "events_calendar":
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        horizon = now + timedelta(days=400)
        rows = (await db.execute(
            select(Event)
            .where(Event.date_start >= now - timedelta(days=1), Event.date_start <= horizon)
            .order_by(Event.date_start)
        )).scalars().all()
        if not rows:
            return fallback_html
        items = []
        for e in rows:
            d = e.date_start
            label = d.strftime("%d %b %Y").upper()
            items.append(f"<li>{label} — {e.title}</li>")
        from app.settings import PUBLIC_BASE_URL as _P
        return (
            "<p>Upcoming training and events:</p><ul>" + "".join(items) + "</ul>"
            f'<p>Full schedule any time at <a href="{_P}/events">{_P}/events</a>.</p>'
        )
    return fallback_html


@router.get("/sections/list", response_class=JSONResponse, response_model=None)
@require_auth
async def newsletter_sections_list(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the section-template palette (per-issue + recurring), for the composer."""
    user = _user(request)
    _require_s1(user)
    rows = (await db.execute(
        select(NewsletterSectionTemplate)
        .where(NewsletterSectionTemplate.is_active.is_(True))
        .order_by(NewsletterSectionTemplate.default_order)
    )).scalars().all()
    out = [{
        "id": r.id, "key": r.key, "title": r.title, "category": r.category,
        "preload": r.preload, "dynamic": bool(r.dynamic_source), "order": r.default_order,
    } for r in rows]
    return JSONResponse({"sections": out})


@router.get("/sections/{key}/render", response_class=JSONResponse, response_model=None)
@require_auth
async def newsletter_section_render(request: Request, key: str, db: AsyncSession = Depends(get_db)):
    """Return a single section as composer-ready HTML (header + body), resolving
    dynamic sections (e.g. live training calendar)."""
    user = _user(request)
    _require_s1(user)
    tpl = (await db.execute(
        select(NewsletterSectionTemplate).where(NewsletterSectionTemplate.key == key)
    )).scalar_one_or_none()
    if not tpl:
        return JSONResponse({"error": "Unknown section."}, status_code=404)
    body = tpl.body_html
    if tpl.dynamic_source:
        body = await _render_dynamic_section(db, tpl.dynamic_source, tpl.body_html)
    # Section block: ◆ heading + body, wrapped so it's a draggable unit in the editor.
    html = f'<h2>\u25c6 {tpl.title}</h2>{body}<p><br></p>'
    return JSONResponse({"key": tpl.key, "title": tpl.title, "html": html, "category": tpl.category})


@router.get("/sections/preload", response_class=JSONResponse, response_model=None)
@require_auth
async def newsletter_sections_preload(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the pre-load recurring block (rendered, in order) for new drafts."""
    user = _user(request)
    _require_s1(user)
    rows = (await db.execute(
        select(NewsletterSectionTemplate)
        .where(NewsletterSectionTemplate.is_active.is_(True),
               NewsletterSectionTemplate.preload.is_(True))
        .order_by(NewsletterSectionTemplate.default_order)
    )).scalars().all()
    chunks = []
    for tpl in rows:
        body = tpl.body_html
        if tpl.dynamic_source:
            body = await _render_dynamic_section(db, tpl.dynamic_source, tpl.body_html)
        chunks.append(f'<h2>\u25c6 {tpl.title}</h2>{body}<p><br></p>')
    return JSONResponse({"html": "".join(chunks)})
