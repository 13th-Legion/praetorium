"""S6 Comms Crypto — shackle code, one-time pad, and trench code endpoints.

Mounted at /api/s6/crypto. Read/encode/decode gated to _can_view (Command, HQ
Team Lead, S6 shop head); generation gated to _can_edit (Command, S6 shop head).
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_auth
from app.clock import now_ct
from app.database import get_db
from app.models.s6_crypto import (
    S6OtpPad,
    S6OtpPage,
    S6ShackleKey,
    S6TrenchCodebook,
    S6TrenchEntry,
)
from app.routes.s6_admin import _can_edit, _can_view, _current_member
from app.services import s6_crypto as crypto

router = APIRouter(prefix="/api/s6/crypto", tags=["s6-crypto"])
templates = Jinja2Templates(directory="app/templates")


async def _gate(request: Request, db: AsyncSession, edit: bool) -> tuple[dict | None, object | None]:
    user = get_current_user(request)
    member = await _current_member(request, db)
    ok = _can_edit(user, member) if edit else _can_view(user, member)
    if not ok:
        raise HTTPException(status_code=403, detail="Restricted")
    return user, member


async def _current_shackle(db: AsyncSession) -> S6ShackleKey | None:
    return (await db.execute(select(S6ShackleKey).order_by(S6ShackleKey.id.desc()).limit(1))).scalar_one_or_none()


async def _current_codebook(db: AsyncSession) -> S6TrenchCodebook | None:
    return (await db.execute(select(S6TrenchCodebook).order_by(S6TrenchCodebook.id.desc()).limit(1))).scalar_one_or_none()


def _secrets_token(n: int) -> str:
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(n))


@router.get("", response_class=HTMLResponse)
@require_auth
async def s6_crypto_page(request: Request, db: AsyncSession = Depends(get_db)):
    user, member = await _gate(request, db, edit=False)
    shackle = await _current_shackle(db)
    pads = (await db.execute(select(S6OtpPad).order_by(S6OtpPad.id.desc()))).scalars().all()
    codebooks = (await db.execute(select(S6TrenchCodebook).order_by(S6TrenchCodebook.id.desc()))).scalars().all()
    cb = await _current_codebook(db)
    entries: list[tuple[str, str]] = []
    if cb:
        rows = (await db.execute(select(S6TrenchEntry).where(S6TrenchEntry.codebook_id == cb.id).order_by(S6TrenchEntry.phrase))).scalars().all()
        entries = [(e.phrase, e.code_group) for e in rows]
    return templates.TemplateResponse("pages/s6_crypto.html", {
        "request": request,
        "user": user,
        "can_edit": _can_edit(user, member),
        "shackle": shackle,
        "shackle_map": crypto.shackle_map(shackle.word, shackle.offset) if shackle else {},
        "shackle_label": crypto.shackle_label(shackle.word, shackle.offset) if shackle else "",
        "nato": crypto.NATO,
        "pads": pads,
        "codebooks": codebooks,
        "current_codebook": cb,
        "entries": entries,
    })


# ─── Shackle code ────────────────────────────────────────────────────────────

@router.post("/shackle/generate")
@require_auth
async def shackle_generate(request: Request, db: AsyncSession = Depends(get_db)):
    user, member = await _gate(request, db, edit=True)
    word, offset = crypto.new_shackle_key()
    db.add(S6ShackleKey(word=word, offset=offset, created_at=now_ct(), created_by=user.get("username")))
    await db.commit()
    return HTMLResponse('<span style="color:#2e7d32;">✓ New shackle key generated</span>')


@router.get("/shackle/pdf")
@require_auth
async def shackle_pdf(request: Request, db: AsyncSession = Depends(get_db)):
    await _gate(request, db, edit=False)
    shackle = await _current_shackle(db)
    if not shackle:
        raise HTTPException(status_code=404, detail="No shackle key")
    data = crypto.shackle_pdf_bytes(shackle.word, shackle.offset)
    return Response(content=data, media_type="application/pdf",
                    headers={'Content-Disposition': 'attachment; filename="Shackle_Code.pdf"'})


@router.post("/shackle/encode")
@require_auth
async def shackle_encode(request: Request, text: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _gate(request, db, edit=False)
    shackle = await _current_shackle(db)
    if not shackle:
        return HTMLResponse("no shackle key")
    return HTMLResponse(crypto.shackle_encode(shackle.word, shackle.offset, text))


@router.post("/shackle/decode")
@require_auth
async def shackle_decode(request: Request, text: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _gate(request, db, edit=False)
    shackle = await _current_shackle(db)
    if not shackle:
        return HTMLResponse("no shackle key")
    return HTMLResponse(crypto.shackle_decode(shackle.word, shackle.offset, text))


# ─── One-time pad ────────────────────────────────────────────────────────────

@router.post("/otp/generate")
@require_auth
async def otp_generate(
    request: Request,
    pages: int = Form(10),
    groups: int = Form(20),
    db: AsyncSession = Depends(get_db),
):
    user, member = await _gate(request, db, edit=True)
    pages = max(1, min(pages, 50))
    groups = max(4, min(groups, 40))
    serial = f"OTP-{now_ct().strftime('%Y%m%d')}-{_secrets_token(4)}"
    pad = S6OtpPad(serial=serial, created_at=now_ct(), created_by=user.get("username"))
    db.add(pad)
    await db.flush()
    for i in range(1, pages + 1):
        db.add(S6OtpPage(pad_id=pad.id, page_number=i, content=crypto.otp_generate_page(groups)))
    await db.commit()
    return HTMLResponse('<span style="color:#2e7d32;">✓ Pad generated</span>')


@router.get("/otp/pdf/{pad_id}")
@require_auth
async def otp_pdf(request: Request, pad_id: int, db: AsyncSession = Depends(get_db)):
    await _gate(request, db, edit=False)
    pad = (await db.execute(select(S6OtpPad).where(S6OtpPad.id == pad_id))).scalar_one_or_none()
    if not pad:
        raise HTTPException(status_code=404, detail="Pad not found")
    pages = (await db.execute(select(S6OtpPage).where(S6OtpPage.pad_id == pad.id).order_by(S6OtpPage.page_number))).scalars().all()
    data = crypto.otp_pdf_bytes(pad.serial, [p.content for p in pages])
    return Response(content=data, media_type="application/pdf",
                    headers={'Content-Disposition': f'attachment; filename="{pad.serial}.pdf"'})


@router.post("/otp/{pad_id}/burn")
@require_auth
async def otp_burn(request: Request, pad_id: int, db: AsyncSession = Depends(get_db)):
    await _gate(request, db, edit=True)
    pad = (await db.execute(select(S6OtpPad).where(S6OtpPad.id == pad_id))).scalar_one_or_none()
    if pad:
        pad.burned = True
        await db.commit()
    return HTMLResponse('<span style="color:#888;">✓ Pad marked burned</span>')


@router.post("/otp/encrypt")
@require_auth
async def otp_encrypt(request: Request, text: str = Form(""), key: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _gate(request, db, edit=False)
    if not key:
        return HTMLResponse("enter a pad key")
    return HTMLResponse(crypto.otp_encrypt(text, key))


@router.post("/otp/decrypt")
@require_auth
async def otp_decrypt(request: Request, text: str = Form(""), key: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _gate(request, db, edit=False)
    if not key:
        return HTMLResponse("enter a pad key")
    return HTMLResponse(crypto.otp_decrypt(text, key))


# ─── Trench code ─────────────────────────────────────────────────────────────

@router.post("/trench/generate")
@require_auth
async def trench_generate(
    request: Request,
    title: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user, member = await _gate(request, db, edit=True)
    title = (title or "").strip() or f"Codebook {now_ct().strftime('%d%b%y')}"
    cb = S6TrenchCodebook(title=title, created_at=now_ct(), created_by=user.get("username"))
    db.add(cb)
    await db.flush()
    for phrase, code in crypto.trench_entries():
        db.add(S6TrenchEntry(codebook_id=cb.id, phrase=phrase, code_group=code))
    await db.commit()
    return HTMLResponse('<span style="color:#2e7d32;">✓ Codebook generated</span>')


@router.get("/trench/pdf/{codebook_id}")
@require_auth
async def trench_pdf(request: Request, codebook_id: int, db: AsyncSession = Depends(get_db)):
    await _gate(request, db, edit=False)
    cb = (await db.execute(select(S6TrenchCodebook).where(S6TrenchCodebook.id == codebook_id))).scalar_one_or_none()
    if not cb:
        raise HTTPException(status_code=404, detail="Codebook not found")
    rows = (await db.execute(select(S6TrenchEntry).where(S6TrenchEntry.codebook_id == cb.id).order_by(S6TrenchEntry.phrase))).scalars().all()
    data = crypto.trench_pdf_bytes(cb.title, [(e.phrase, e.code_group) for e in rows])
    return Response(content=data, media_type="application/pdf",
                    headers={'Content-Disposition': 'attachment; filename="Trench_Code.pdf"'})


@router.post("/trench/encode")
@require_auth
async def trench_encode(request: Request, text: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _gate(request, db, edit=False)
    cb = await _current_codebook(db)
    if not cb:
        return HTMLResponse("no codebook")
    rows = (await db.execute(select(S6TrenchEntry).where(S6TrenchEntry.codebook_id == cb.id))).scalars().all()
    return HTMLResponse(crypto.trench_encode([(e.phrase, e.code_group) for e in rows], text))


@router.post("/trench/decode")
@require_auth
async def trench_decode(request: Request, text: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _gate(request, db, edit=False)
    cb = await _current_codebook(db)
    if not cb:
        return HTMLResponse("no codebook")
    rows = (await db.execute(select(S6TrenchEntry).where(S6TrenchEntry.codebook_id == cb.id))).scalars().all()
    return HTMLResponse(crypto.trench_decode([(e.phrase, e.code_group) for e in rows], text))
