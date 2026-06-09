"""TRADOC management — CRUD for training blocks, subjects, and markdown docs.

Roles allowed: command, s3, admin.
"""

import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func

from app.auth import require_role, get_current_user
from app.database import async_session
from app.models.training import TradocBlock, TradocItem

log = logging.getLogger(__name__)

router = APIRouter(prefix="/training/tradoc/manage", tags=["tradoc-admin"])

TRADOC_MANAGE_ROLES = ("command", "s3", "admin")
VALID_DOC_TYPES = {"none", "markdown", "pdf", "external", "page"}


def _form_int(form, key, default=0):
    try:
        return int((form.get(key) or "").strip())
    except (ValueError, AttributeError):
        return default


# ─── Blocks ──────────────────────────────────────────────────────────────────

@router.post("/block/create")
@require_role(*TRADOC_MANAGE_ROLES)
async def block_create(request: Request):
    form = await request.form()
    number = _form_int(form, "number", None)
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    sort_order = _form_int(form, "sort_order", 0)
    if number is None:
        raise HTTPException(status_code=400, detail="Block number required")
    if not name:
        raise HTTPException(status_code=400, detail="Block name required")
    async with async_session() as db:
        existing = (await db.execute(
            select(TradocBlock).where(TradocBlock.number == number)
        )).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail=f"Block number {number} already exists")
        db.add(TradocBlock(
            number=number, name=name,
            description=description or None,
            sort_order=sort_order if sort_order else (99 if number == 0 else number),
        ))
        await db.commit()
    log.info(f"TRADOC block created: {number} '{name}'")
    return RedirectResponse(url="/training/tradoc", status_code=303)


@router.post("/block/{block_id}/edit")
@require_role(*TRADOC_MANAGE_ROLES)
async def block_edit(request: Request, block_id: int):
    form = await request.form()
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    sort_order = _form_int(form, "sort_order", 0)
    if not name:
        raise HTTPException(status_code=400, detail="Block name required")
    async with async_session() as db:
        blk = (await db.execute(
            select(TradocBlock).where(TradocBlock.id == block_id)
        )).scalar_one_or_none()
        if not blk:
            raise HTTPException(status_code=404, detail="Block not found")
        blk.name = name
        blk.description = description or None
        if sort_order:
            blk.sort_order = sort_order
        # keep denormalized block_name on items in sync
        items = (await db.execute(
            select(TradocItem).where(TradocItem.block == blk.number)
        )).scalars().all()
        for it in items:
            it.block_name = name
        await db.commit()
    log.info(f"TRADOC block {block_id} edited -> '{name}'")
    return RedirectResponse(url="/training/tradoc", status_code=303)


@router.post("/block/{block_id}/archive")
@require_role(*TRADOC_MANAGE_ROLES)
async def block_archive(request: Request, block_id: int):
    """Soft-delete (archive) or restore a block. Hides its subjects from the page."""
    form = await request.form()
    unarchive = bool((form.get("unarchive") or "").strip())
    async with async_session() as db:
        blk = (await db.execute(
            select(TradocBlock).where(TradocBlock.id == block_id)
        )).scalar_one_or_none()
        if not blk:
            raise HTTPException(status_code=404, detail="Block not found")
        blk.archived = not unarchive
        await db.commit()
    log.info(f"TRADOC block {block_id} archived={blk.archived}")
    return RedirectResponse(url="/training/tradoc", status_code=303)


# ─── Subjects (TradocItem) ────────────────────────────────────────────────────

@router.post("/subject/create")
@require_role(*TRADOC_MANAGE_ROLES)
async def subject_create(request: Request):
    form = await request.form()
    block = _form_int(form, "block", None)
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    sort_order = _form_int(form, "sort_order", 0)
    optional = bool((form.get("optional") or "").strip())
    doc_type = (form.get("doc_type") or "none").strip()
    doc_title = (form.get("doc_title") or "").strip()
    doc_url = (form.get("doc_url") or "").strip()
    doc_body = form.get("doc_body") or ""
    if block is None:
        raise HTTPException(status_code=400, detail="Block required")
    if not name:
        raise HTTPException(status_code=400, detail="Subject name required")
    if doc_type not in VALID_DOC_TYPES:
        raise HTTPException(status_code=400, detail="Invalid doc type")
    async with async_session() as db:
        blk = (await db.execute(
            select(TradocBlock).where(TradocBlock.number == block)
        )).scalar_one_or_none()
        if not blk:
            raise HTTPException(status_code=400, detail="Block does not exist")
        if not sort_order:
            maxso = (await db.execute(
                select(func.coalesce(func.max(TradocItem.sort_order), 0))
            )).scalar() or 0
            sort_order = maxso + 1
        db.add(TradocItem(
            block=block, block_name=blk.name, name=name,
            description=description or None,
            sort_order=sort_order,
            optional=optional,
            doc_type=doc_type,
            doc_title=doc_title or None,
            doc_url=doc_url or None,
            doc_body=doc_body or None,
        ))
        await db.commit()
    log.info(f"TRADOC subject created: BLK{block} '{name}' ({doc_type})")
    return RedirectResponse(url="/training/tradoc", status_code=303)


@router.post("/subject/{item_id}/edit")
@require_role(*TRADOC_MANAGE_ROLES)
async def subject_edit(request: Request, item_id: int):
    form = await request.form()
    block = _form_int(form, "block", None)
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    sort_order = _form_int(form, "sort_order", 0)
    optional = bool((form.get("optional") or "").strip())
    doc_type = (form.get("doc_type") or "none").strip()
    doc_title = (form.get("doc_title") or "").strip()
    doc_url = (form.get("doc_url") or "").strip()
    doc_body = form.get("doc_body") or ""
    if doc_type not in VALID_DOC_TYPES:
        raise HTTPException(status_code=400, detail="Invalid doc type")
    if not name:
        raise HTTPException(status_code=400, detail="Subject name required")
    async with async_session() as db:
        it = (await db.execute(
            select(TradocItem).where(TradocItem.id == item_id)
        )).scalar_one_or_none()
        if not it:
            raise HTTPException(status_code=404, detail="Subject not found")
        if block is not None:
            blk = (await db.execute(
                select(TradocBlock).where(TradocBlock.number == block)
            )).scalar_one_or_none()
            if not blk:
                raise HTTPException(status_code=400, detail="Block does not exist")
            it.block = block
            it.block_name = blk.name
        it.name = name
        it.description = description or None
        if sort_order:
            it.sort_order = sort_order
        it.optional = optional
        it.doc_type = doc_type
        it.doc_title = doc_title or None
        it.doc_url = doc_url or None
        it.doc_body = doc_body or None
        await db.commit()
    log.info(f"TRADOC subject {item_id} edited -> '{name}'")
    return RedirectResponse(url="/training/tradoc", status_code=303)


@router.post("/subject/{item_id}/archive")
@require_role(*TRADOC_MANAGE_ROLES)
async def subject_archive(request: Request, item_id: int):
    """Soft-delete (archive) or restore a subject. Sign-off history is preserved."""
    form = await request.form()
    unarchive = bool((form.get("unarchive") or "").strip())
    async with async_session() as db:
        it = (await db.execute(
            select(TradocItem).where(TradocItem.id == item_id)
        )).scalar_one_or_none()
        if not it:
            raise HTTPException(status_code=404, detail="Subject not found")
        it.archived = not unarchive
        await db.commit()
    log.info(f"TRADOC subject {item_id} archived={it.archived}")
    return RedirectResponse(url="/training/tradoc", status_code=303)
