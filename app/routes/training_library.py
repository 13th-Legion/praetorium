"""Training Library — TRADOC documents and Battle Library (FMs, TCs, ATPs)."""

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth import require_auth, require_role, get_current_user
from app.database import async_session
from app.models.training import TradocItem
from app.models.library import LibraryDocument

# ── Battle Library storage config ────────────────────────────────────────────
LIBRARY_DIR = Path("/app/data/library")
MAX_LIBRARY_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MANAGE_ROLES = ("command", "s1_lead", "s3", "admin")

# Category code → display metadata for the Battle Library page
LIBRARY_CATEGORIES = [
    {"code": "FM", "category": "Field Manuals (FM)", "icon": "📗"},
    {"code": "TC", "category": "Training Circulars (TC)", "icon": "📘"},
    {"code": "ATP", "category": "Army Techniques Publications (ATP)", "icon": "📙"},
    {"code": "TM", "category": "Technical Manuals (TM)", "icon": "📕"},
    {"code": "Other", "category": "Other Publications", "icon": "📚"},
]
VALID_CATEGORY_CODES = {c["code"] for c in LIBRARY_CATEGORIES}

log = logging.getLogger(__name__)

router = APIRouter(prefix="/training", tags=["training-library"])
templates = Jinja2Templates(directory="app/templates")

# ─── TRADOC web pages (slug → page config) ──────────────────────────────────
# Each entry maps a URL slug to its template partial + metadata.

TRADOC_PAGES = {
    "drill-ceremony": {
        "template": "tradoc/dc.html",
        "title": "Drill & Ceremony SOP",
        "block_num": 1,
        "block_name": "Theory & Medical",
        "pdf_url": "/static/tradoc/drill-ceremony.pdf",
    },
    "brm": {
        "template": "tradoc/brm.html",
        "title": "Basic Rifle Marksmanship Training Guide",
        "block_num": 2,
        "block_name": "Weapons Qualification",
        "pdf_url": "/static/tradoc/brm-training-guide.pdf",
    },
    "use-of-force": {
        "template": "tradoc/uof.html",
        "title": "Use of Force SOP",
        "block_num": 2,
        "block_name": "Weapons Qualification",
        "pdf_url": "/static/tradoc/use-of-force.pdf",
    },
    "course-of-fire": {
        "template": "tradoc/cof.html",
        "title": "Basic Course of Fire",
        "block_num": 2,
        "block_name": "Weapons Qualification",
        "pdf_url": "/static/tradoc/course-of-fire.pdf",
    },
    "landnav-basic": {
        "template": "tradoc/landnav_basic.html",
        "title": "Basic Land Navigation",
        "block_num": 3,
        "block_name": "Supplemental Skills",
        "pdf_url": "",
    },
    "landnav-intermediate": {
        "template": "tradoc/landnav_intermediate.html",
        "title": "Intermediate Land Navigation",
        "block_num": 3,
        "block_name": "Supplemental Skills",
        "pdf_url": "",
    },
    "landnav-advanced": {
        "template": "tradoc/landnav_advanced.html",
        "title": "Advanced Land Navigation",
        "block_num": 3,
        "block_name": "Supplemental Skills",
        "pdf_url": "",
    },
    "landnav-expert": {
        "template": "tradoc/landnav_expert.html",
        "title": "Expert Land Navigation",
        "block_num": 3,
        "block_name": "Supplemental Skills",
        "pdf_url": "",
    },
    "recon": {
        "template": "tradoc/recon.html",
        "title": "Field Reconnaissance (13LP 2-1)",
        "block_num": 4,
        "block_name": "Combat Fundamentals",
        "pdf_url": "/static/tradoc/recon101.pdf",
    },
}

# ─── TRADOC subject document links ──────────────────────────────────────────
# Maps TRADOC subject names to their document info.
# type: "page" = internal web page, "external" = off-site link, "pdf" = direct PDF

TRADOC_DOCS = {
    # ─── Block 1: Theory & Medical ────────────────────────────────────────

    "Drill & Ceremony": {
        "url": "/training/tradoc/drill-ceremony",
        "pdf_url": "/static/tradoc/drill-ceremony.pdf",
        "type": "page",
        "title": "Drill & Ceremony SOP",
    },
    "Gear Review": {
        "url": "/static/tradoc/uniform-sop.pdf",
        "type": "pdf",
        "title": "TSM Uniform SOP",
    },
    # ─── Block 2: Weapons Qualification ───────────────────────────────────

    "Basic Rifle Marksmanship": {
        "url": "/training/tradoc/brm",
        "pdf_url": "/static/tradoc/brm-training-guide.pdf",
        "type": "page",
        "title": "BRM Training Guide",
    },
    "Rifle Qualification": {
        "url": "/training/tradoc/course-of-fire",
        "pdf_url": "/static/tradoc/course-of-fire.pdf",
        "type": "page",
        "title": "Basic Course of Fire",
    },
    "Shooting Drills": {
        "url": "/training/tradoc/course-of-fire",
        "pdf_url": "/static/tradoc/course-of-fire.pdf",
        "type": "page",
        "title": "Basic Course of Fire",
    },
    "Use of Force": {
        "url": "/training/tradoc/use-of-force",
        "pdf_url": "/static/tradoc/use-of-force.pdf",
        "type": "page",
        "title": "Use of Force SOP",
    },
    # ─── Block 3: Supplemental Skills ─────────────────────────────────────
    "Basic Land Navigation": {
        "url": "/training/tradoc/landnav-basic",
        "type": "page",
        "title": "Basic Land Navigation",
    },
    "Intermediate Land Navigation": {
        "url": "/training/tradoc/landnav-intermediate",
        "type": "page",
        "title": "Intermediate Land Navigation",
    },
    "Advanced Land Navigation": {
        "url": "/training/tradoc/landnav-advanced",
        "type": "page",
        "title": "Advanced Land Navigation",
    },
    "Expert Land Navigation": {
        "url": "/training/tradoc/landnav-expert",
        "type": "page",
        "title": "Expert Land Navigation",
    },
    # ─── Block 4: Combat Fundamentals ─────────────────────────────────────
    "Recon 101": {
        "url": "/training/tradoc/recon",
        "pdf_url": "/static/tradoc/recon101.pdf",
        "type": "page",
        "title": "Field Reconnaissance (13LP 2-1)",
    },
}

# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/tradoc", response_class=HTMLResponse)
@require_auth
async def tradoc_page(request: Request):
    """TRADOC training document library organized by basic training block."""
    user = get_current_user(request)

    async with async_session() as db:
        result = await db.execute(
            select(TradocItem).order_by(TradocItem.block, TradocItem.sort_order)
        )
        items = result.scalars().all()

    blocks = {}
    for item in items:
        if item.block == 0:
            continue
        if item.block not in blocks:
            blocks[item.block] = {
                "number": item.block,
                "name": item.block_name,
                "subjects": [],
            }
        doc = TRADOC_DOCS.get(item.name)
        blocks[item.block]["subjects"].append({
            "name": item.name,
            "description": item.description,
            "doc": doc,
        })

    return templates.TemplateResponse("pages/tradoc.html", {
        "request": request,
        "user": user,
        "blocks": sorted(blocks.values(), key=lambda b: b["number"]),
        "doc_count": len(TRADOC_DOCS),
        "total_subjects": sum(len(b["subjects"]) for b in blocks.values()),
    })


@router.get("/tradoc/{slug}", response_class=HTMLResponse)
@require_auth
async def tradoc_doc_page(request: Request, slug: str):
    """Render a single TRADOC training document."""
    user = get_current_user(request)

    page = TRADOC_PAGES.get(slug)
    if not page:
        raise HTTPException(status_code=404, detail="Document not found")

    pdf_url = page.get("pdf_url", "")
    pdf_external = pdf_url.startswith("http")

    return templates.TemplateResponse("pages/tradoc_doc.html", {
        "request": request,
        "user": user,
        "doc_title": page["title"],
        "block_num": page["block_num"],
        "block_name": page["block_name"],
        "pdf_url": pdf_url,
        "pdf_external": pdf_external,
        "tradoc_template": page["template"],
    })


def _can_manage_library(user) -> bool:
    """True if the user holds a role allowed to manage Battle Library docs."""
    if not user:
        return False
    return bool(set(user.get("roles", [])).intersection(set(MANAGE_ROLES)))


@router.get("/library", response_class=HTMLResponse)
@require_auth
async def battle_library_page(request: Request):
    """Battle Library — FMs, TCs, ATPs, and other reference publications."""
    user = get_current_user(request)

    async with async_session() as db:
        result = await db.execute(
            select(LibraryDocument).order_by(
                LibraryDocument.sort_order, LibraryDocument.pub_number
            )
        )
        docs = result.scalars().all()

    # Group docs into the fixed category buckets (preserve display order).
    by_code = {c["code"]: [] for c in LIBRARY_CATEGORIES}
    for d in docs:
        code = d.category if d.category in by_code else "Other"
        by_code[code].append({
            "id": d.id,
            "number": d.pub_number,
            "title": d.title,
            "pub_date": d.pub_date,
            "url": f"/training/library/{d.id}/file",
            "category": d.category,
            "sort_order": d.sort_order,
        })

    categories = [
        {"category": c["category"], "code": c["code"], "icon": c["icon"],
         "pubs": by_code[c["code"]]}
        for c in LIBRARY_CATEGORIES
    ]

    return templates.TemplateResponse("pages/battle_library.html", {
        "request": request,
        "user": user,
        "categories": categories,
        "total_pubs": len(docs),
        "can_manage": _can_manage_library(user),
        "manage_categories": LIBRARY_CATEGORIES,
    })


@router.get("/library/{doc_id}/file")
@require_auth
async def battle_library_file(request: Request, doc_id: int):
    """Serve a Battle Library PDF inline (all authed members)."""
    async with async_session() as db:
        doc = (await db.execute(
            select(LibraryDocument).where(LibraryDocument.id == doc_id)
        )).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(doc.stored_path)
    if not path.is_file():
        log.error(f"Library file missing on disk: {doc.stored_path}")
        raise HTTPException(status_code=404, detail="File not found on disk")
    safe_name = (doc.original_filename or doc.filename or "document.pdf")
    return FileResponse(
        str(path),
        media_type=doc.mime_type or "application/pdf",
        filename=safe_name,
        content_disposition_type="inline",
    )


@router.post("/library/upload")
@require_role(*MANAGE_ROLES)
async def battle_library_upload(request: Request):
    """Upload a new Battle Library publication."""
    user = get_current_user(request)
    form = await request.form()
    upload: UploadFile = form.get("file")
    category = (form.get("category") or "").strip()
    pub_number = (form.get("pub_number") or "").strip()
    title = (form.get("title") or "").strip()
    pub_date = (form.get("pub_date") or "").strip()

    if not upload or not getattr(upload, "filename", None):
        raise HTTPException(status_code=400, detail="No file provided")
    if category not in VALID_CATEGORY_CODES:
        raise HTTPException(status_code=400, detail="Invalid category")
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    orig = upload.filename
    if not orig.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    data = await upload.read()
    if len(data) > MAX_LIBRARY_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File exceeds 50MB limit")

    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.pdf"
    stored_path = LIBRARY_DIR / stored_name
    stored_path.write_bytes(data)

    async with async_session() as db:
        doc = LibraryDocument(
            category=category,
            pub_number=pub_number or "—",
            title=title,
            pub_date=pub_date or None,
            filename=stored_name,
            original_filename=orig,
            stored_path=str(stored_path),
            file_size=len(data),
            mime_type=upload.content_type or "application/pdf",
            uploaded_by=(user or {}).get("username") or (user or {}).get("nc_username") or "unknown",
        )
        db.add(doc)
        await db.commit()
    log.info(f"Battle Library upload: {category} {pub_number} '{title}' by {doc.uploaded_by}")
    return RedirectResponse(url="/training/library", status_code=303)


@router.post("/library/{doc_id}/edit")
@require_role(*MANAGE_ROLES)
async def battle_library_edit(request: Request, doc_id: int):
    """Edit Battle Library doc metadata (and optionally replace the file)."""
    form = await request.form()
    category = (form.get("category") or "").strip()
    pub_number = (form.get("pub_number") or "").strip()
    title = (form.get("title") or "").strip()
    pub_date = (form.get("pub_date") or "").strip()
    sort_order_raw = (form.get("sort_order") or "").strip()
    upload = form.get("file")

    async with async_session() as db:
        doc = (await db.execute(
            select(LibraryDocument).where(LibraryDocument.id == doc_id)
        )).scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        if category:
            if category not in VALID_CATEGORY_CODES:
                raise HTTPException(status_code=400, detail="Invalid category")
            doc.category = category
        if title:
            doc.title = title
        if pub_number:
            doc.pub_number = pub_number
        if pub_date:
            doc.pub_date = pub_date
        if sort_order_raw:
            try:
                doc.sort_order = int(sort_order_raw)
            except ValueError:
                pass

        # Optional file replacement
        if upload and getattr(upload, "filename", None):
            if not upload.filename.lower().endswith(".pdf"):
                raise HTTPException(status_code=400, detail="Only PDF files are allowed")
            data = await upload.read()
            if len(data) > MAX_LIBRARY_FILE_SIZE:
                raise HTTPException(status_code=400, detail="File exceeds 50MB limit")
            old_path = Path(doc.stored_path)
            LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
            stored_name = f"{uuid.uuid4().hex}.pdf"
            new_path = LIBRARY_DIR / stored_name
            new_path.write_bytes(data)
            doc.filename = stored_name
            doc.original_filename = upload.filename
            doc.stored_path = str(new_path)
            doc.file_size = len(data)
            doc.mime_type = upload.content_type or "application/pdf"
            try:
                if old_path.is_file():
                    old_path.unlink()
            except OSError as e:
                log.warning(f"Could not remove replaced library file {old_path}: {e}")

        await db.commit()
    return RedirectResponse(url="/training/library", status_code=303)


@router.post("/library/{doc_id}/delete")
@require_role(*MANAGE_ROLES)
async def battle_library_delete(request: Request, doc_id: int):
    """Delete a Battle Library doc (DB row + file on disk)."""
    async with async_session() as db:
        doc = (await db.execute(
            select(LibraryDocument).where(LibraryDocument.id == doc_id)
        )).scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        path = Path(doc.stored_path)
        await db.delete(doc)
        await db.commit()
    try:
        if path.is_file():
            path.unlink()
    except OSError as e:
        log.warning(f"Could not remove library file {path}: {e}")
    return RedirectResponse(url="/training/library", status_code=303)
