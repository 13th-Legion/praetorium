"""Training claim submission & review — members request TRADOC/cert/award sign-offs."""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db
from config import get_settings

log = logging.getLogger("training_claims")
from app.models.member import Member
from app.models.training import (
    TradocItem, MemberTradoc, Certification, MemberCertification, TrainingClaim
)
from app.models.awards import MemberAward

UPLOADS_DIR = Path("/app/uploads/training_claims")
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".doc", ".docx"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# NC WebDAV base for personnel file archive
NC_PERSONNEL_BASE = "/remote.php/dav/files/spooky/13th%20Legion%20Shared/%5bS-1%5d%20Admin/Personnel"
from app.settings import NC_SVC_USER as NC_ARCHIVE_USER, NC_SVC_PASS as NC_ARCHIVE_PASS


async def _ensure_nc_folders(nc_url: str, *path_parts: str):
    """Create nested NC WebDAV folders (idempotent)."""
    async with httpx.AsyncClient(timeout=10) as client:
        current = f"{nc_url}{NC_PERSONNEL_BASE}"
        for part in path_parts:
            current = f"{current}/{quote(part)}"
            await client.request("MKCOL", f"{current}/", auth=(NC_ARCHIVE_USER, NC_ARCHIVE_PASS))


async def _upload_to_nc(nc_url: str, folder_parts: list[str], filename: str, file_bytes: bytes):
    """Upload a file to NC under Personnel/{folder_parts}/{filename}."""
    await _ensure_nc_folders(nc_url, *folder_parts)
    path = f"{nc_url}{NC_PERSONNEL_BASE}"
    for part in folder_parts:
        path = f"{path}/{quote(part)}"
    path = f"{path}/{quote(filename)}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(path, content=file_bytes, auth=(NC_ARCHIVE_USER, NC_ARCHIVE_PASS))
        if resp.status_code in (201, 204):
            log.info(f"Uploaded to NC: {'/'.join(folder_parts)}/{filename}")
        else:
            log.warning(f"NC upload returned {resp.status_code}: {'/'.join(folder_parts)}/{filename}")


async def _archive_training_doc(member: "Member", claim: "TrainingClaim"):
    """Upload approved training claim attachment to NC Personnel/{Name}/Certifications/."""
    if not claim.doc_path:
        return

    file_path = Path(claim.doc_path)
    if not file_path.exists():
        log.warning(f"Claim #{claim.id} doc_path not found: {claim.doc_path}")
        return

    settings = get_settings()
    member_folder = f"{member.last_name}, {member.first_name}"

    # Build descriptive filename
    ext = file_path.suffix
    item_name = claim.reference_name or f"item_{claim.reference_id}"
    safe_item = "".join(c if c.isalnum() or c in " -_" else "_" for c in item_name).strip()
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    archive_name = f"{claim.claim_type}_{safe_item}_{date_str}{ext}"

    try:
        await _upload_to_nc(
            settings.nc_url,
            [member_folder, "Certifications"],
            archive_name,
            file_path.read_bytes(),
        )
    except Exception as e:
        log.error(f"Failed to archive claim #{claim.id} to NC: {e}")

router = APIRouter(prefix="/api/training", tags=["training"])
templates = Jinja2Templates(directory="app/templates")

REVIEWER_ROLES = {"command", "admin", "s1", "leader"}


def _is_reviewer(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & REVIEWER_ROLES)


# ─── Cert detail modal (public to any authenticated user) ───────────────────

@router.get("/cert/{cert_id}", response_class=HTMLResponse)
@require_auth
async def cert_detail(request: Request, cert_id: int, db: AsyncSession = Depends(get_db)):
    """Return cert detail HTML for modal display."""
    result = await db.execute(select(Certification).where(Certification.id == cert_id))
    cert = result.scalar_one_or_none()
    if not cert:
        return HTMLResponse('<p style="color:#c62828;">Certification not found.</p>')

    # Format criteria as list with category headers
    criteria_html = ""
    if cert.criteria:
        items = cert.criteria.replace("\\n", "\n").split("\n")
        criteria_html = '<div style="margin-top:12px;"><div style="font-weight:700;color:#d4a537;margin-bottom:6px;">Requirements</div>'
        for item in items:
            item = item.strip()
            if not item:
                criteria_html += '<div style="height:8px;"></div>'
                continue
            if not item.startswith("•") and item.endswith(":"):
                # Category header
                criteria_html += f'<div style="font-size:13px;font-weight:600;color:#d4a537;padding:6px 0 2px 0;">{item}</div>'
            else:
                item = item.lstrip("•").strip()
                criteria_html += f'<div style="font-size:13px;padding:2px 0 2px 16px;color:#ccc;">• {item}</div>'
        criteria_html += '</div>'

    # Format resources as list with clickable links
    resources_html = ""
    if cert.resources:
        import re
        items = cert.resources.replace("\\n", "\n").split("\n")
        resources_html = '<div style="margin-top:12px;"><div style="font-weight:700;color:#d4a537;margin-bottom:6px;">Resources</div>'
        for item in items:
            item = item.strip().lstrip("•").strip()
            if not item:
                continue
            # Make URLs clickable
            item = re.sub(r'(https?://\S+)', r'<a href="\1" target="_blank" style="color:#6fa8dc;">\1</a>', item)
            resources_html += f'<div style="font-size:13px;padding:3px 0;color:#ccc;">• {item}</div>'
        resources_html += '</div>'

    category_labels = {
        "marksmanship": "Marksmanship",
        "search_rescue": "Search & Rescue",
        "leadership": "Leadership",
        "specialty": "Specialty",
        "elite": "Elite",
        "communications": "Communications",
    }

    html = f"""
    <div style="font-size:36px;text-align:center;margin-bottom:8px;">{cert.icon or ''}</div>
    <div style="text-align:center;font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">{category_labels.get(cert.category, cert.category)}</div>
    <div style="text-align:center;font-size:20px;font-weight:700;color:#eee;margin-bottom:8px;">{cert.name}</div>
    <div style="font-size:13px;color:#aaa;line-height:1.6;">{cert.description or 'No description available.'}</div>
    {criteria_html}
    {resources_html}
    """
    return HTMLResponse(html)


# ─── Member: View own training progress (dashboard widget) ──────────────────

@router.get("/progress", response_class=HTMLResponse)
@require_auth
async def training_progress_widget(request: Request, db: AsyncSession = Depends(get_db)):
    """Dashboard widget showing the user's training progress."""
    user = get_current_user(request)
    username = user["username"]

    result = await db.execute(select(Member).where(Member.nc_username == username))
    member = result.scalar_one_or_none()

    if not member:
        return HTMLResponse('<p class="text-muted">No personnel record found.</p>')

    # TRADOC
    total_result = await db.execute(select(TradocItem))
    all_items = total_result.scalars().all()
    completed_result = await db.execute(
        select(MemberTradoc).where(MemberTradoc.member_id == member.id)
    )
    completed = {mt.item_id for mt in completed_result.scalars().all()}
    total = len(all_items)
    done = len(completed)
    pct = round(done / total * 100) if total > 0 else 0

    # Certs
    certs_result = await db.execute(select(Certification))
    all_certs = certs_result.scalars().all()
    earned_result = await db.execute(
        select(MemberCertification).where(MemberCertification.member_id == member.id)
    )
    earned_ids = {mc.certification_id for mc in earned_result.scalars().all()}

    # Pending claims
    claims_result = await db.execute(
        select(TrainingClaim)
        .where(TrainingClaim.member_id == member.id, TrainingClaim.status == "pending")
    )
    pending_claims = claims_result.scalars().all()

    # TRADOC progress bar
    if member.status == "active":
        tradoc_html = '<div style="color:#2e7d32;font-weight:600;">✅ Patched</div>'
    else:
        bar_color = "#2e7d32" if pct >= 75 else "#ef6c00" if pct >= 40 else "#c62828"
        tradoc_html = f"""
        <div style="font-size:12px;color:#aaa;margin-bottom:4px;">TRADOC: {done}/{total}</div>
        <div style="height:8px;background:rgba(255,255,255,0.1);border-radius:4px;overflow:hidden;">
            <div style="width:{pct}%;height:100%;background:{bar_color};border-radius:4px;"></div>
        </div>"""

    # Certs summary
    earned_count = len(earned_ids)
    total_certs = len(all_certs)
    cert_icons = "".join(c.icon for c in all_certs if c.id in earned_ids)
    certs_html = f'<div style="margin-top:8px;font-size:12px;color:#aaa;">Certs: {earned_count}/{total_certs} {cert_icons}</div>'

    # Pending claims count
    pending_html = ""
    if pending_claims:
        pending_html = f'<div style="margin-top:8px;font-size:12px;color:#ef6c00;">⏳ {len(pending_claims)} pending claim{"s" if len(pending_claims) > 1 else ""}</div>'

    # ── To-Dos ──
    todos = []

    # Document signing
    if not member.nda_signed_at:
        todos.append(('<a href="/api/s1/documents/sign/nda" style="color:#d4a537;">Sign NDA</a>', '📝'))
    if not member.waiver_signed_at:
        todos.append(('<a href="/api/s1/documents/sign/general_waiver" style="color:#d4a537;">Sign Liability Waiver</a>', '📝'))

    # TRADOC for recruits
    if member.status == "recruit" and done < total:
        remaining = total - done
        todos.append((f'{remaining} TRADOC item{"s" if remaining > 1 else ""} remaining', '🎯'))

    # Request training sign-off
    if member.status == "recruit" and done > 0 and not pending_claims:
        todos.append(('<a href="/api/training/claim" style="color:#d4a537;">Request training sign-off</a>', '🎓'))

    todos_html = ""
    if todos:
        items = "".join(
            f'<div style="display:flex;align-items:center;gap:6px;padding:4px 0;font-size:12px;">'
            f'<span>{icon}</span><span>{text}</span></div>'
            for text, icon in todos
        )
        todos_html = f"""
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.08);">
            <div style="font-size:11px;font-weight:700;color:#aaa;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">To-Do</div>
            {items}
        </div>"""
    else:
        todos_html = """
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.08);">
            <div style="font-size:12px;color:#2e7d32;">✅ All caught up — nothing pending</div>
        </div>"""

    return HTMLResponse(f"""
    {tradoc_html}
    {certs_html}
    {pending_html}
    {todos_html}
    <div style="margin-top:10px;text-align:center;">
        <a href="/profile" style="color:#d4a537;font-size:12px;text-decoration:none;">View Full Training Record →</a>
    </div>
    """)


# ─── Member: Submit training claim ──────────────────────────────────────────

@router.get("/claim", response_class=HTMLResponse)
@require_auth
async def claim_form(request: Request, db: AsyncSession = Depends(get_db)):
    """Show form for submitting a training claim."""
    user = get_current_user(request)
    username = user["username"]

    result = await db.execute(select(Member).where(Member.nc_username == username))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Get uncompleted TRADOC items
    completed_result = await db.execute(
        select(MemberTradoc.item_id).where(MemberTradoc.member_id == member.id)
    )
    completed_ids = {row[0] for row in completed_result.all()}

    tradoc_result = await db.execute(select(TradocItem).order_by(TradocItem.sort_order))
    tradoc_items = [t for t in tradoc_result.scalars().all() if t.id not in completed_ids]

    # Get unearned certs
    earned_result = await db.execute(
        select(MemberCertification.certification_id).where(MemberCertification.member_id == member.id)
    )
    earned_ids = {row[0] for row in earned_result.all()}

    comms_sort = case((Certification.category == "communications", 1), else_=0)
    cert_result = await db.execute(
        select(Certification).order_by(comms_sort, Certification.sort_order, Certification.name)
    )
    certs = [c for c in cert_result.scalars().all() if c.id not in earned_ids]

    # Get un-attended past FTXs
    from app.models.events import Event, EventRSVP
    attended_result = await db.execute(
        select(EventRSVP.event_id).where(EventRSVP.member_id == member.id, EventRSVP.attended == True)
    )
    attended_ids = {row[0] for row in attended_result.all()}

    ftx_result = await db.execute(
        select(Event)
        .where(Event.category.in_(["ftx", "mcftx"]))
        .where(Event.date_start < datetime.utcnow())
        .order_by(desc(Event.date_start))
    )
    ftx_events = [e for e in ftx_result.scalars().all() if e.id not in attended_ids]

    # Get existing pending claims with resolved names
    pending_result = await db.execute(
        select(TrainingClaim)
        .where(TrainingClaim.member_id == member.id, TrainingClaim.status == "pending")
    )
    pending_raw = pending_result.scalars().all()

    # Build lookup dicts for names
    all_tradoc_result = await db.execute(select(TradocItem))
    tradoc_names = {t.id: t.name for t in all_tradoc_result.scalars().all()}
    all_certs_result = await db.execute(select(Certification))
    cert_names = {c.id: f"{c.icon} {c.name}" for c in all_certs_result.scalars().all()}
    from app.models.events import Event
    all_ftx_result = await db.execute(select(Event).where(Event.category.in_(["ftx", "mcftx"])))
    ftx_names = {e.id: e.title for e in all_ftx_result.scalars().all()}

    pending = []
    for c in pending_raw:
        if c.claim_type == "tradoc":
            name = tradoc_names.get(c.reference_id, f"Item #{c.reference_id}")
        elif c.claim_type == "ftx_attendance":
            name = ftx_names.get(c.reference_id, f"FTX: {c.reference_id}")
        else:
            name = cert_names.get(c.reference_id, f"Cert #{c.reference_id}")
        pending.append({"claim": c, "name": name})

    return templates.TemplateResponse("pages/training_claim.html", {
        "request": request,
        "user": user,
        "member": member,
        "tradoc_items": tradoc_items,
        "certs": certs,
        "ftx_events": ftx_events,
        "pending": pending,
    })


@router.post("/claim")
@require_auth
async def submit_claim(request: Request, db: AsyncSession = Depends(get_db)):
    """Submit a training claim for review, with optional file upload."""
    user = get_current_user(request)
    username = user["username"]

    result = await db.execute(select(Member).where(Member.nc_username == username))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    form = await request.form()
    claim_type = form.get("claim_type", "")  # tradoc, certification, ftx_attendance
    description = form.get("description", "").strip()
    callsign = form.get("callsign", "").strip().upper()

    if claim_type not in ("tradoc", "certification", "ftx_attendance"):
        raise HTTPException(status_code=400, detail="Invalid claim type")

    # Handle file upload (shared across all claims in this submission)
    doc_path = None
    upload_file: UploadFile = form.get("document")
    if upload_file and hasattr(upload_file, "filename") and upload_file.filename:
        ext = Path(upload_file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File type {ext} not allowed. Use: {', '.join(ALLOWED_EXTENSIONS)}")
        content = await upload_file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large (max 10MB)")
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = f"{member.id}_{uuid.uuid4().hex[:8]}_{upload_file.filename}"
        file_path = UPLOADS_DIR / safe_name
        file_path.write_bytes(content)
        doc_path = str(file_path)

    # Collect reference IDs — TRADOC can be multi-select, cert is single
    reference_ids = []
    if claim_type == "tradoc":
        reference_ids = [int(v) for v in form.getlist("tradoc_ids") if v]
    elif claim_type == "ftx_attendance":
        ftx_id = form.get("ftx_id", "")
        if ftx_id:
            reference_ids = [int(ftx_id)]
    else:
        cert_id = form.get("cert_id", "")
        if cert_id:
            reference_ids = [int(cert_id)]

    if not reference_ids:
        raise HTTPException(status_code=400, detail="Select at least one item to claim")

    # Require callsign for communications certs
    if claim_type == "certification" and reference_ids:
        cert_result = await db.execute(
            select(Certification).where(Certification.id == reference_ids[0])
        )
        cert_obj = cert_result.scalar_one_or_none()
        if cert_obj and cert_obj.category == "communications" and not callsign:
            raise HTTPException(status_code=400, detail="FCC callsign is required for radio certifications")

    # Create one claim per item, skip duplicates
    created = 0
    for ref_id in reference_ids:
        existing = await db.execute(
            select(TrainingClaim).where(
                TrainingClaim.member_id == member.id,
                TrainingClaim.claim_type == claim_type,
                TrainingClaim.reference_id == ref_id,
                TrainingClaim.status == "pending",
            )
        )
        if existing.scalar_one_or_none():
            continue  # skip duplicate, don't error on batch

        # For comms certs, store callsign as the description (used by _sync_radio_cert on approval)
        claim_desc = description
        if claim_type == "certification" and callsign:
            claim_desc = callsign if not description else f"{callsign}\n{description}"

        claim = TrainingClaim(
            member_id=member.id,
            claim_type=claim_type,
            reference_id=ref_id,
            description=claim_desc,
            doc_path=doc_path if created == 0 else None,  # attach doc to first claim only
        )
        db.add(claim)
        created += 1

    if created == 0:
        raise HTTPException(status_code=409, detail="All selected items already have pending claims")

    await db.commit()

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/training/claim?submitted=1", status_code=303)


@router.get("/claims/{claim_id}/document")
@require_auth
async def download_claim_document(request: Request, claim_id: int, db: AsyncSession = Depends(get_db)):
    """Download the document attached to a training claim."""
    user = get_current_user(request)

    result = await db.execute(select(TrainingClaim).where(TrainingClaim.id == claim_id))
    claim = result.scalar_one_or_none()
    if not claim or not claim.doc_path:
        raise HTTPException(status_code=404, detail="Document not found")

    # Members can download their own; reviewers can download any
    member_result = await db.execute(select(Member).where(Member.nc_username == user["username"]))
    requester = member_result.scalar_one_or_none()

    if not _is_reviewer(user) and (not requester or requester.id != claim.member_id):
        raise HTTPException(status_code=403, detail="Access denied")

    file_path = Path(claim.doc_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Extract original filename from the stored path
    filename = file_path.name.split("_", 2)[-1] if "_" in file_path.name else file_path.name

    return FileResponse(file_path, filename=filename)


# ─── S1/Command: Review claims ──────────────────────────────────────────────

@router.get("/claims/review")
@require_auth
async def claims_review(request: Request, db: AsyncSession = Depends(get_db)):
    """S1/Command review queue for training claims."""
    user = get_current_user(request)
    if not _is_reviewer(user):
        raise HTTPException(status_code=403, detail="Reviewer access required")

    # Pending claims with member info
    result = await db.execute(
        select(TrainingClaim, Member)
        .join(Member, TrainingClaim.member_id == Member.id)
        .where(TrainingClaim.status == "pending")
        .order_by(TrainingClaim.created_at)
    )
    pending = result.all()

    # Recent reviewed
    reviewed_result = await db.execute(
        select(TrainingClaim, Member)
        .join(Member, TrainingClaim.member_id == Member.id)
        .where(TrainingClaim.status.in_(["approved", "denied"]))
        .order_by(desc(TrainingClaim.reviewed_at))
        .limit(20)
    )
    reviewed = reviewed_result.all()

    # Build claim display data with item names
    tradoc_cache = {}
    cert_cache = {}

    ftx_cache = {}
    async def get_item_name(claim):
        if claim.claim_type == "tradoc":
            if claim.reference_id not in tradoc_cache:
                r = await db.execute(select(TradocItem).where(TradocItem.id == claim.reference_id))
                item = r.scalar_one_or_none()
                tradoc_cache[claim.reference_id] = item.name if item else f"Item #{claim.reference_id}"
            return tradoc_cache[claim.reference_id]
        elif claim.claim_type == "ftx_attendance":
            if claim.reference_id not in ftx_cache:
                from app.models.events import Event
                r = await db.execute(select(Event).where(Event.id == claim.reference_id))
                event = r.scalar_one_or_none()
                ftx_cache[claim.reference_id] = f"FTX: {event.title}" if event else f"FTX #{claim.reference_id}"
            return ftx_cache[claim.reference_id]
        else:
            if claim.reference_id not in cert_cache:
                r = await db.execute(select(Certification).where(Certification.id == claim.reference_id))
                cert = r.scalar_one_or_none()
                cert_cache[claim.reference_id] = f"{cert.icon} {cert.name}" if cert else f"Cert #{claim.reference_id}"
            return cert_cache[claim.reference_id]

    pending_data = []
    for claim, member in pending:
        item_name = await get_item_name(claim)
        pending_data.append({
            "claim": claim,
            "member": member,
            "item_name": item_name,
        })

    reviewed_data = []
    for claim, member in reviewed:
        item_name = await get_item_name(claim)
        reviewed_data.append({
            "claim": claim,
            "member": member,
            "item_name": item_name,
        })

    return templates.TemplateResponse("pages/training_review.html", {
        "request": request,
        "user": user,
        "pending": pending_data,
        "reviewed": reviewed_data,
    })


# Map cert names to member model fields for radio licenses
_RADIO_CERT_MAP = {
    "HAM Technician": ("ham_callsign", "ham_license_class", "Technician"),
    "HAM General": ("ham_callsign", "ham_license_class", "General"),
    "HAM Extra": ("ham_callsign", "ham_license_class", "Extra"),
    "GMRS License": ("gmrs_callsign", None, None),
}


async def _sync_radio_cert(claim: "TrainingClaim", db: "AsyncSession"):
    """If the approved cert is a radio license, update the member's comms fields."""
    if not claim.reference_id:
        return
    cert_result = await db.execute(
        select(Certification).where(Certification.id == claim.reference_id)
    )
    cert = cert_result.scalar_one_or_none()
    if not cert or cert.name not in _RADIO_CERT_MAP:
        return

    member_result = await db.execute(
        select(Member).where(Member.id == claim.member_id)
    )
    member = member_result.scalar_one_or_none()
    if not member:
        return

    callsign_field, class_field, class_value = _RADIO_CERT_MAP[cert.name]

    # Extract callsign from first line of claim description (member provides it when submitting)
    callsign = (claim.description or "").strip().split("\n")[0].strip().upper()
    if callsign:
        setattr(member, callsign_field, callsign)
    if class_field and class_value:
        setattr(member, class_field, class_value)

    member.updated_at = datetime.utcnow()


@router.post("/claims/{claim_id}/approve")
@require_auth
async def approve_claim(request: Request, claim_id: int, db: AsyncSession = Depends(get_db)):
    """Approve a training claim — creates the actual sign-off/cert record."""
    user = get_current_user(request)
    if not _is_reviewer(user):
        raise HTTPException(status_code=403, detail="Reviewer access required")

    result = await db.execute(select(TrainingClaim).where(TrainingClaim.id == claim_id))
    claim = result.scalar_one_or_none()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    nc_user = user.get("username", "unknown")

    # Look up approver's rank + name from members table
    approver_result = await db.execute(select(Member).where(Member.nc_username == nc_user))
    approver = approver_result.scalar_one_or_none()
    if approver:
        from app.constants import RANK_ABBR
        rank = RANK_ABBR.get(approver.rank_grade, "")
        reviewer = f"{rank} {approver.last_name}".strip()
    else:
        reviewer = user.get("display_name", nc_user)

    claim.status = "approved"
    claim.reviewed_by = reviewer
    claim.reviewed_at = datetime.utcnow()

    # Create the actual record
    if claim.claim_type == "tradoc":
        signoff = MemberTradoc(
            member_id=claim.member_id,
            item_id=claim.reference_id,
            signed_off_by=reviewer,
            notes=f"Approved from training claim #{claim.id}",
        )
        db.add(signoff)
    elif claim.claim_type == "certification":
        cert_award = MemberCertification(
            member_id=claim.member_id,
            certification_id=claim.reference_id,
            awarded_by=reviewer,
            notes=f"Approved from training claim #{claim.id}",
        )
        db.add(cert_award)

        # Auto-populate comms fields on member profile for radio certs
        await _sync_radio_cert(claim, db)
    elif claim.claim_type == "ftx_attendance":
        from app.models.events import EventRSVP, Event
        rsvp_result = await db.execute(select(EventRSVP).where(EventRSVP.event_id == claim.reference_id, EventRSVP.member_id == claim.member_id))
        rsvp = rsvp_result.scalar_one_or_none()
        if not rsvp:
            rsvp = EventRSVP(
                event_id=claim.reference_id,
                member_id=claim.member_id,
                status="attending",
                checked_in=True,
                checked_in_at=datetime.utcnow(),
                checked_in_by=reviewer,
                attended=True
            )
            db.add(rsvp)
        else:
            rsvp.attended = True
            rsvp.checked_in = True
            if not rsvp.checked_in_by:
                rsvp.checked_in_by = reviewer
                rsvp.checked_in_at = datetime.utcnow()
        
        # update last_ftx
        evt_result = await db.execute(select(Event).where(Event.id == claim.reference_id))
        evt = evt_result.scalar_one_or_none()
        if evt and evt.date_start:
            member_result = await db.execute(select(Member).where(Member.id == claim.member_id))
            member_obj = member_result.scalar_one_or_none()
            if member_obj:
                if not member_obj.last_ftx or evt.date_start.date() > member_obj.last_ftx:
                    member_obj.last_ftx = evt.date_start.date()

    await db.commit()

    # Archive attachment to NC (best-effort, don't block approval on failure)
    if claim.doc_path:
        # Look up member and reference name for archive filename
        member_result = await db.execute(select(Member).where(Member.id == claim.member_id))
        member = member_result.scalar_one_or_none()
        if member:
            # Get reference name
            ref_name = None
            if claim.claim_type == "tradoc" and claim.reference_id:
                item_result = await db.execute(select(TradocItem).where(TradocItem.id == claim.reference_id))
                item = item_result.scalar_one_or_none()
                ref_name = item.name if item else None
            elif claim.claim_type == "certification" and claim.reference_id:
                cert_result = await db.execute(select(Certification).where(Certification.id == claim.reference_id))
                cert = cert_result.scalar_one_or_none()
                ref_name = cert.name if cert else None
            claim.reference_name = ref_name  # temp attr for archive function
            try:
                await _archive_training_doc(member, claim)
            except Exception as e:
                log.error(f"NC archive failed for claim #{claim.id}: {e}")

    return HTMLResponse(f'<span style="color:#2e7d32;font-weight:600;">✅ Approved by {reviewer}</span>')


@router.post("/claims/{claim_id}/deny")
@require_auth
async def deny_claim(request: Request, claim_id: int, db: AsyncSession = Depends(get_db)):
    """Deny a training claim."""
    user = get_current_user(request)
    if not _is_reviewer(user):
        raise HTTPException(status_code=403, detail="Reviewer access required")

    form = await request.form()
    notes = form.get("notes", "").strip()
    reviewer = user.get("display_name", user.get("username", "unknown"))

    result = await db.execute(select(TrainingClaim).where(TrainingClaim.id == claim_id))
    claim = result.scalar_one_or_none()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    claim.status = "denied"
    claim.reviewed_by = reviewer
    claim.reviewed_at = datetime.utcnow()
    claim.review_notes = notes

    await db.commit()

    return HTMLResponse(f'<span style="color:#c62828;font-weight:600;">❌ Denied by {reviewer}</span>')


@router.post("/claims/{claim_id}/revoke")
@require_auth
async def revoke_claim(request: Request, claim_id: int, db: AsyncSession = Depends(get_db)):
    """Revoke an approved training claim — deletes the sign-off/cert record and resets claim to denied."""
    user = get_current_user(request)
    if not _is_reviewer(user):
        raise HTTPException(status_code=403, detail="Reviewer access required")

    result = await db.execute(select(TrainingClaim).where(TrainingClaim.id == claim_id))
    claim = result.scalar_one_or_none()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    if claim.status != "approved":
        return HTMLResponse('<span style="color:#c62828;">Only approved claims can be revoked</span>')

    reviewer = user.get("display_name", user.get("username", "unknown"))

    # Delete the actual sign-off/cert record that was created on approval
    if claim.claim_type == "tradoc" and claim.reference_id:
        await db.execute(
            select(MemberTradoc).where(
                MemberTradoc.member_id == claim.member_id,
                MemberTradoc.item_id == claim.reference_id,
            )
        )
        # Use delete statement
        from sqlalchemy import delete
        await db.execute(
            delete(MemberTradoc).where(
                MemberTradoc.member_id == claim.member_id,
                MemberTradoc.item_id == claim.reference_id,
            )
        )
    elif claim.claim_type == "certification" and claim.reference_id:
        from sqlalchemy import delete
        await db.execute(
            delete(MemberCertification).where(
                MemberCertification.member_id == claim.member_id,
                MemberCertification.certification_id == claim.reference_id,
            )
        )

    # Mark claim as revoked
    claim.status = "denied"
    claim.reviewed_by = reviewer
    claim.reviewed_at = datetime.utcnow()
    claim.review_notes = f"Revoked by {reviewer}"

    await db.commit()

    return HTMLResponse(f'<span style="color:#ff8f00;font-weight:600;">↩️ Revoked by {reviewer}</span>')
