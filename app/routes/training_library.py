"""Training Library — TRADOC documents and Battle Library (FMs, TCs, ATPs)."""

import logging
import uuid
from pathlib import Path

import bleach
import markdown as md
from fastapi import APIRouter, Request, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth import require_auth, require_role, get_current_user
from app.database import async_session
from app.models.training import TradocBlock, TradocItem, TradocTier
from app.models.library import LibraryDocument

# ── Markdown rendering (TRADOC docs) ─────────────────────────────────────────
# Allowed HTML tags after markdown conversion (sanitized with bleach).
_MD_ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    "p", "pre", "hr", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td", "img", "span", "div",
]
_MD_ALLOWED_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "td": ["align"], "th": ["align"],
}


def render_markdown(text: str) -> str:
    """Convert markdown to sanitized HTML for TRADOC doc pages."""
    if not text:
        return ""
    html = md.markdown(
        text,
        extensions=["extra", "sane_lists", "nl2br", "toc", "tables", "fenced_code"],
    )
    clean = bleach.clean(html, tags=_MD_ALLOWED_TAGS, attributes=_MD_ALLOWED_ATTRS, strip=True)
    return clean


TRADOC_MANAGE_ROLES = ("command", "s3", "admin")


def _can_manage_tradoc(user) -> bool:
    if not user:
        return False
    return bool(set(user.get("roles", [])).intersection(set(TRADOC_MANAGE_ROLES)))

# ── Battle Library storage config ────────────────────────────────────────────
LIBRARY_DIR = Path("/app/data/library")
MAX_LIBRARY_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MANAGE_ROLES = ("command", "s1_lead", "s3", "admin")

# Category code → display metadata for the Battle Library page
# Publication categories now come from the library_categories table
# (taxonomies service). Call the helpers for live data.
from app.services import taxonomies as _tax


def LIBRARY_CATEGORIES() -> list[dict]:
    return _tax.library_categories()


def VALID_CATEGORY_CODES() -> set[str]:
    return _tax.valid_library_codes()

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


# ─── Routes ──────────────────────────────────────────────────────────────────

def _item_doc(item) -> dict | None:
    """Build the doc dict for a subject from its DB doc fields, or None."""
    dtype = (item.doc_type or "none")
    if dtype == "none" or (not item.doc_url and not item.doc_body and dtype != "markdown"):
        # markdown with empty body still counts as a doc page if doc_body set below
        if not (dtype == "markdown" and item.doc_body):
            return None
    if dtype == "markdown":
        if not item.doc_body:
            return None
        return {"type": "markdown", "title": item.doc_title or item.name,
                "url": f"/training/tradoc/{item.id}"}
    if dtype in ("pdf", "external"):
        if not item.doc_url:
            return None
        return {"type": dtype, "title": item.doc_title or item.name, "url": item.doc_url}
    if dtype == "page":
        if not item.doc_url:
            return None
        return {"type": "page", "title": item.doc_title or item.name, "url": item.doc_url}
    return None


@router.get("/tradoc", response_class=HTMLResponse)
@require_auth
async def tradoc_page(request: Request):
    """TRADOC training document library organized by basic training block."""
    user = get_current_user(request)
    can_manage = _can_manage_tradoc(user)

    async with async_session() as db:
        block_rows = (await db.execute(
            select(TradocBlock).order_by(TradocBlock.sort_order, TradocBlock.number)
        )).scalars().all()
        item_rows = (await db.execute(
            select(TradocItem).order_by(TradocItem.block, TradocItem.sort_order)
        )).scalars().all()
        tier_rows = (await db.execute(
            select(TradocTier).order_by(TradocTier.sort_order, TradocTier.id)
        )).scalars().all()

    # Group items by block number
    items_by_block = {}
    for item in item_rows:
        items_by_block.setdefault(item.block, []).append(item)

    blocks = []
    doc_count = 0
    total_subjects = 0
    for blk in block_rows:
        if blk.archived and not can_manage:
            continue
        subjects = []
        for item in items_by_block.get(blk.number, []):
            if item.archived and not can_manage:
                continue
            doc = _item_doc(item)
            if doc and not item.archived:
                doc_count += 1
            if not item.archived:
                total_subjects += 1
            subjects.append({
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "sort_order": item.sort_order,
                "optional": item.optional,
                "archived": item.archived,
                "doc": doc,
                "doc_type": item.doc_type or "none",
                "doc_title": item.doc_title or "",
                "doc_url": item.doc_url or "",
                "doc_body": item.doc_body or "",
            })
        blocks.append({
            "id": blk.id,
            "number": blk.number,
            "name": blk.name,
            "description": blk.description or "",
            "sort_order": blk.sort_order,
            "archived": blk.archived,
            "tier": (blk.tier or "initial"),
            "subjects": subjects,
        })

    # ── Group blocks into ordered tiers (categories) ──
    # Tiers now come from the DB (tradoc_tiers) and are fully manageable.
    # `all_tiers` = every tier (incl. archived) for the manage UI dropdown/modal.
    all_tiers = [
        {"id": t.id, "key": t.key, "label": t.label,
         "subtitle": t.subtitle or "", "sort_order": t.sort_order,
         "archived": t.archived}
        for t in tier_rows
    ]
    # Fallback if the tiers table is somehow empty (fresh DB / migration not run).
    if not all_tiers:
        all_tiers = [
            {"id": None, "key": "initial", "label": "Initial Entry Training",
             "subtitle": "The patching pipeline — what every Legionary completes to earn the patch.",
             "sort_order": 0, "archived": False},
            {"id": None, "key": "advanced", "label": "Advanced Qualifications & Tabs",
             "subtitle": "Earned above and beyond patching — statewide courses and the 13th Legion's own tabs.",
             "sort_order": 1, "archived": False},
        ]

    known = {t["key"] for t in all_tiers}
    tiers = []
    for meta in all_tiers:
        if meta["archived"]:
            continue
        tier_blocks = [b for b in blocks if b["tier"] == meta["key"]]
        if tier_blocks:
            tiers.append({**meta, "blocks": tier_blocks})
    # Any block pointing at an unknown/removed tier falls into the first tier bucket
    # so it never silently disappears.
    orphan_blocks = [b for b in blocks if b["tier"] not in known]
    if orphan_blocks:
        if tiers:
            tiers[0]["blocks"].extend(orphan_blocks)
        else:
            first = next((t for t in all_tiers if not t["archived"]), all_tiers[0])
            tiers.append({**first, "blocks": orphan_blocks})

    return templates.TemplateResponse("pages/tradoc.html", {
        "request": request,
        "user": user,
        "blocks": blocks,
        "tiers": tiers,
        "all_tiers": all_tiers,
        "doc_count": doc_count,
        "total_subjects": total_subjects,
        "can_manage": can_manage,
    })


@router.get("/tradoc/{slug}", response_class=HTMLResponse)
@require_auth
async def tradoc_doc_page(request: Request, slug: str):
    """Render a single TRADOC training document.

    If `slug` is numeric it is a TradocItem id (DB markdown doc). Otherwise it
    falls back to the legacy hardcoded HTML template pages in TRADOC_PAGES.
    """
    user = get_current_user(request)

    # Numeric slug => DB-backed markdown doc
    if slug.isdigit():
        async with async_session() as db:
            item = (await db.execute(
                select(TradocItem).where(TradocItem.id == int(slug))
            )).scalar_one_or_none()
        if not item or item.doc_type != "markdown" or not item.doc_body:
            raise HTTPException(status_code=404, detail="Document not found")
        return templates.TemplateResponse("pages/tradoc_doc.html", {
            "request": request,
            "user": user,
            "doc_title": item.doc_title or item.name,
            "block_num": item.block,
            "block_name": item.block_name,
            "pdf_url": "",
            "pdf_external": False,
            "tradoc_template": None,
            "markdown_html": render_markdown(item.doc_body),
        })

    # Legacy hardcoded template page
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
        "markdown_html": None,
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
    by_code = {c["code"]: [] for c in LIBRARY_CATEGORIES()}
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
        for c in LIBRARY_CATEGORIES()
    ]

    return templates.TemplateResponse("pages/battle_library.html", {
        "request": request,
        "user": user,
        "categories": categories,
        "total_pubs": len(docs),
        "can_manage": _can_manage_library(user),
        "manage_categories": LIBRARY_CATEGORIES(),
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
    # ETag from file size + mtime so a swapped file (new mtime/size) busts browser cache.
    st = path.stat()
    etag = f'"{doc.id}-{st.st_size}-{int(st.st_mtime)}"'
    if request.headers.get("if-none-match") == etag:
        from fastapi.responses import Response
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache, must-revalidate"})
    return FileResponse(
        str(path),
        media_type=doc.mime_type or "application/pdf",
        filename=safe_name,
        content_disposition_type="inline",
        headers={"ETag": etag, "Cache-Control": "no-cache, must-revalidate"},
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
    if category not in VALID_CATEGORY_CODES():
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
            if category not in VALID_CATEGORY_CODES():
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
