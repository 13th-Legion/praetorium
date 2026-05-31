"""Training Library — TRADOC documents and Battle Library (FMs, TCs, ATPs)."""

import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth import require_auth, get_current_user
from app.database import async_session
from app.models.training import TradocItem

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

# ─── Battle Library publications ─────────────────────────────────────────────

BATTLE_LIBRARY = [
    {
        "category": "Field Manuals (FM)",
        "icon": "📗",
        "pubs": [],
    },
    {
        "category": "Training Circulars (TC)",
        "icon": "📘",
        "pubs": [],
    },
    {
        "category": "Army Techniques Publications (ATP)",
        "icon": "📙",
        "pubs": [],
    },
    {
        "category": "Technical Manuals (TM)",
        "icon": "📕",
        "pubs": [],
    },
]


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


@router.get("/library", response_class=HTMLResponse)
@require_auth
async def battle_library_page(request: Request):
    """Battle Library — FMs, TCs, ATPs, and other reference publications."""
    user = get_current_user(request)

    total_pubs = sum(len(cat["pubs"]) for cat in BATTLE_LIBRARY)

    return templates.TemplateResponse("pages/battle_library.html", {
        "request": request,
        "user": user,
        "categories": BATTLE_LIBRARY,
        "total_pubs": total_pubs,
    })
