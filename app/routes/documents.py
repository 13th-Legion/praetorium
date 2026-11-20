"""S1 signables management — document CRUD + who-signed tracking (PP-430)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.document import Document
from app.models.member import Member
from app.models.recruiting import DocumentSignature

router = APIRouter(prefix="/api/s1/documents", tags=["s1-documents"])
templates = Jinja2Templates(directory="app/templates")

# slug -> member *_signed_at denormal column (kept in sync by the sign route).
_SIGNED_ATTR = {
    "activity_policy": "activity_policy_signed_at",
    "code_of_conduct": "code_of_conduct_signed_at",
    "bylaws": "bylaws_signed_at",
    "nda": "nda_signed_at",
    "general_waiver": "waiver_signed_at",
}


async def _ensure_documents(db: AsyncSession) -> None:
    """Seed the 5 signables from the legacy text constants if the table is empty."""
    if (await db.execute(select(Document).limit(1))).scalars().first():
        return
    from app.routes.doc_texts import CODE_OF_CONDUCT_TEXT, BYLAWS_TEXT, activity_policy_html
    from app.routes.s1_admin import NDA_TEXT, WAIVER_TEXT
    defaults = [
        ("activity_policy", "Activity Policy", activity_policy_html(), 1),
        ("code_of_conduct", "Code of Conduct", CODE_OF_CONDUCT_TEXT, 2),
        ("bylaws", "TSM By-Laws", BYLAWS_TEXT, 3),
        ("nda", "Non-Disclosure Agreement", NDA_TEXT, 4),
        ("general_waiver", "General Waiver & Release of Liability", WAIVER_TEXT, 5),
    ]
    for slug, title, content, order in defaults:
        db.add(Document(slug=slug, title=title, content_html=content, sort_order=order, version=1))
    await db.commit()


def _require_s1(user: dict) -> None:
    roles = set(user.get("roles", []))
    if not (roles & {"s1", "command", "admin"}):
        raise HTTPException(status_code=403, detail="Command or S1 access required")


@router.get("/status", response_class=HTMLResponse)
@require_auth
async def document_status(request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user", {})
    _require_s1(user)
    await _ensure_documents(db)

    docs = (await db.execute(select(Document).order_by(Document.archived, Document.sort_order, Document.id))).scalars().all()
    members = (
        await db.execute(select(Member).where(Member.status.in_(["recruit", "active"])).order_by(Member.last_name, Member.first_name))
    ).scalars().all()

    # current-version signatures per doc (document_id or legacy document_type link)
    sig_rows = (await db.execute(select(DocumentSignature))).scalars().all()

    rows = []
    for doc in docs:
        attr = _SIGNED_ATTR.get(doc.slug)
        signed_ids = set()
        for s in sig_rows:
            if (s.document_id == doc.id) or (s.document_id is None and s.document_type == doc.slug):
                if str(s.document_version) == str(doc.version):
                    signed_ids.add(s.member_id)
        # legacy: v1 doc, no signature row, but the denormal column is set
        signed = []
        for m in members:
            if m.id in signed_ids:
                signed.append(m)
            elif doc.version == 1 and attr and getattr(m, attr, None):
                signed.append(m)
        signed_set = {m.id for m in signed}
        outstanding = [m for m in members if m.id not in signed_set]
        rows.append({
            "doc": doc,
            "signed": signed,
            "outstanding": outstanding,
            "signed_count": len(signed),
            "outstanding_count": len(outstanding),
        })

    docs_json = [
        {"id": d.id, "slug": d.slug, "title": d.title, "content_html": d.content_html}
        for d in docs
    ]
    return templates.TemplateResponse(
        "pages/s1_documents.html",
        {"request": request, "user": user, "rows": rows, "total_members": len(members), "docs_json": docs_json},
    )


@router.post("/create", response_class=HTMLResponse)
@require_auth
async def document_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user", {})
    _require_s1(user)
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    slug = (form.get("slug") or "").strip().lower().replace(" ", "_") or None
    if not slug:
        slug = "doc_" + datetime.utcnow().strftime("%Y%m%d%H%M%S")
    content = form.get("content") or ""
    existing = (await db.execute(select(Document).where(Document.slug == slug))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="A document with that slug already exists")
    db.add(Document(slug=slug, title=title, content_html=content, sort_order=99, version=1, updated_by=user.get("username")))
    await db.commit()
    return RedirectResponse("/api/s1/documents/status", status_code=303)


@router.post("/{doc_id}/edit", response_class=HTMLResponse)
@require_auth
async def document_edit(request: Request, doc_id: int, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user", {})
    _require_s1(user)
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    form = await request.form()
    title = (form.get("title") or doc.title).strip()
    content = form.get("content") or ""
    doc.title = title
    doc.content_html = content
    doc.version += 1  # editing requires re-sign
    doc.updated_by = user.get("username")
    await db.commit()
    return RedirectResponse("/api/s1/documents/status", status_code=303)


@router.post("/{doc_id}/toggle", response_class=HTMLResponse)
@require_auth
async def document_toggle(request: Request, doc_id: int, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user", {})
    _require_s1(user)
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.active = not doc.active
    doc.updated_by = user.get("username")
    await db.commit()
    return RedirectResponse("/api/s1/documents/status", status_code=303)


@router.post("/{doc_id}/archive", response_class=HTMLResponse)
@require_auth
async def document_archive(request: Request, doc_id: int, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user", {})
    _require_s1(user)
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.archived = True
    doc.active = False
    doc.updated_by = user.get("username")
    await db.commit()
    return RedirectResponse("/api/s1/documents/status", status_code=303)