"""S1 Admin routes — payment, NDA/waiver, recruiter assignment, offboarding."""

import os
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.models.member import Member
from app.models.recruiting import Recruiter, DocumentSignature, SeparationLog

import httpx
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/s1", tags=["s1-admin"])
templates = Jinja2Templates(directory="app/templates")

# Groups with S1 admin access
from app.constants import S1_ROLES, PIPELINE_ROLES

NC_URL = "https://cloud.13thlegion.org"

from app.settings import (
    NC_PORTAL_SVC_USER as NC_SVC_USER,
    NC_PORTAL_SVC_PASS as NC_SVC_PASS,
    NC_SVC_USER as NC_SPOOKY_USER,
    NC_SVC_PASS as NC_SPOOKY_PASS,
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM,
    REAPPLY_URL,
)


def _send_offboard_email(member, reason: str, notes: str | None = None):
    """Send separation notification email to the member."""
    if not member.email:
        logger.warning(f"No email for {member.first_name} {member.last_name}, skipping offboard email")
        return False

    reason_labels = {
        "voluntary": "Voluntary Separation",
        "involuntary": "Involuntary Separation",
        "inactivity": "Separation for Inactivity",
        "blacklisted": "Separation — Blacklisted",
    }
    reason_label = reason_labels.get(reason, reason.title())

    # Build email body based on reason
    if reason == "blacklisted":
        reapply_block = (
            "<p>Based on the nature of your separation, you are <strong>not eligible to reapply</strong> "
            "to the 13th Legion or any Texas State Militia unit.</p>"
        )
    elif reason == "involuntary":
        reapply_block = (
            "<p>If you believe this separation was made in error, you may appeal by contacting "
            "unit Command at <a href='mailto:admin@13thlegion.org'>admin@13thlegion.org</a>.</p>"
            "<p>If you wish to reapply in the future, there is a <strong>90-day waiting period</strong> "
            f"from your separation date. After that period, you may reapply here:</p>"
            f"<p><a href='{REAPPLY_URL}'>{REAPPLY_URL}</a></p>"
        )
    elif reason == "inactivity":
        reapply_block = (
            "<p>We understand life gets busy. If your circumstances change and you'd like to "
            "return, you're welcome to reapply at any time:</p>"
            f"<p><a href='{REAPPLY_URL}'>{REAPPLY_URL}</a></p>"
            "<p>Your previous training records will be reviewed upon reapplication and may be "
            "credited toward your new TRADOC requirements.</p>"
        )
    else:  # voluntary
        reapply_block = (
            "<p>We respect your decision. If you ever want to come back, "
            "you're welcome to reapply at any time:</p>"
            f"<p><a href='{REAPPLY_URL}'>{REAPPLY_URL}</a></p>"
            "<p>Your previous training records will be kept on file and may be "
            "credited toward your new TRADOC requirements upon return.</p>"
        )

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1a1a2e; padding: 20px; text-align: center;">
            <h1 style="color: #d4a537; margin: 0; font-size: 24px;">13th Legion</h1>
            <p style="color: #aaa; margin: 4px 0 0; font-size: 12px;">Texas State Militia</p>
        </div>
        <div style="padding: 24px; background: #f9f9f9; color: #333;">
            <p>Dear {member.first_name},</p>

            <p>This email confirms your separation from the 13th Legion, effective immediately.</p>

            <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
                <tr><td style="padding: 6px 0; font-weight: bold; width: 140px;">Reason:</td>
                    <td style="padding: 6px 0;">{reason_label}</td></tr>
                <tr><td style="padding: 6px 0; font-weight: bold;">Effective Date:</td>
                    <td style="padding: 6px 0;">{datetime.utcnow().strftime('%B %d, %Y')}</td></tr>
            </table>

            {f'<p><strong>Notes:</strong> {notes}</p>' if notes else ''}

            <h3 style="color: #1a1a2e; border-bottom: 1px solid #ddd; padding-bottom: 8px;">What This Means</h3>
            <ul style="color: #555;">
                <li>Your Nextcloud account and portal access have been deactivated</li>
                <li>You have been removed from all unit communication channels</li>
                <li>Per the NDA you signed, confidentiality obligations remain in effect for two (2) years</li>
                <li>Any unit-issued equipment must be returned to your team leader or S4</li>
            </ul>

            <h3 style="color: #1a1a2e; border-bottom: 1px solid #ddd; padding-bottom: 8px;">Reapplication</h3>
            {reapply_block}

            <hr style="border: none; border-top: 1px solid #ddd; margin: 24px 0;">
            <p style="color: #888; font-size: 12px;">
                If you have questions, contact us at
                <a href="mailto:admin@13thlegion.org">admin@13thlegion.org</a>
            </p>
        </div>
    </div>
    """

    plain = f"""Dear {member.first_name},

This email confirms your separation from the 13th Legion, effective immediately.

Reason: {reason_label}
Effective Date: {datetime.utcnow().strftime('%B %d, %Y')}
{f'Notes: {notes}' if notes else ''}

Your Nextcloud account and portal access have been deactivated.
You have been removed from all unit communication channels.
Per the NDA you signed, confidentiality obligations remain in effect for two (2) years.
Any unit-issued equipment must be returned to your team leader or S4.

{'You are not eligible to reapply.' if reason == 'blacklisted' else f'To reapply: {REAPPLY_URL}'}

Questions? Contact admin@13thlegion.org
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"13th Legion — {reason_label}"
    msg["From"] = SMTP_FROM
    msg["To"] = member.email
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [member.email], msg.as_string())
        logger.info(f"Offboard email sent to {member.email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send offboard email to {member.email}: {e}")
        return False


def is_s1(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & S1_ROLES)


def require_s1(user: dict):
    if not is_s1(user):
        raise HTTPException(status_code=403, detail="S1 / Command access required")


def is_pipeline(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & PIPELINE_ROLES)


def require_pipeline(user: dict):
    if not is_pipeline(user):
        raise HTTPException(status_code=403, detail="S1 / Recruiter access required")


# ─── PP-021: Payment Tracking ───────────────────────────────────────────────

@router.get("/payments")
@require_auth
async def payment_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """S1 payment tracking dashboard — shows pipeline applicants, not all members."""
    user = request.session.get("user", {})
    require_s1(user)

    # Fetch applicants from Deck pipeline
    applicants = await _fetch_pipeline_applicants()

    # Also fetch completed (recently onboarded) to show payment status
    url = f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks"
    completed = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            if resp.status_code == 200:
                for stack in resp.json():
                    if stack.get("id") == 16:  # Complete stack
                        for card in stack.get("cards", []):
                            title = card.get("title", "").strip().lstrip("✅📋🔍").strip()
                            completed.append({
                                "id": card["id"],
                                "name": title,
                                "stage": "Complete",
                                "stage_id": 16,
                            })
    except Exception:
        pass

    # For applicants in pipeline, look up payment status from portal DB by name match
    all_pipeline = applicants + completed
    pipeline_with_payment = []
    for app in all_pipeline:
        # Try to match to a member record for payment status
        name_parts = app["name"].split(None, 1)
        member = None
        if len(name_parts) >= 2:
            first, last = name_parts[0], name_parts[1]
            result = await db.execute(
                select(Member).where(
                    Member.first_name == first, Member.last_name == last
                )
            )
            member = result.scalar_one_or_none()

        pipeline_with_payment.append({
            **app,
            "member": member,
            "fee_status": member.app_fee_status if member else "unknown",
            "fee_method": member.app_fee_method if member else None,
            "fee_paid_at": member.app_fee_paid_at if member else None,
            "member_id": member.id if member else None,
        })

    pending = [p for p in pipeline_with_payment if p["fee_status"] in ("pending", "unknown")]
    paid = [p for p in pipeline_with_payment if p["fee_status"] == "paid"]
    waived = [p for p in pipeline_with_payment if p["fee_status"] == "waived"]

    return templates.TemplateResponse("pages/s1_payments.html", {
        "request": request,
        "user": user,
        "pending": pending,
        "paid": paid,
        "waived": waived,
    })


@router.post("/payments/{member_id}/record")
@require_auth
async def record_payment(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Record a payment for a member."""
    user = request.session.get("user", {})
    require_s1(user)

    form = await request.form()
    status = form.get("status", "paid")  # paid or waived
    method = form.get("method", "")

    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    member.app_fee_status = status
    member.app_fee_method = method if status == "paid" else "waived"
    member.app_fee_paid_at = datetime.utcnow() if status == "paid" else None
    await db.commit()

    return HTMLResponse(f"""
        <span style="color: {'#2e7d32' if status == 'paid' else '#1565c0'}; font-weight: 600;">
            {'✅ Paid' if status == 'paid' else '🔵 Waived'} — {method or status}
        </span>
    """)


# ─── PP-020: Digital NDA & General Waiver ────────────────────────────────────

@router.get("/documents/sign/{doc_type}")
@require_auth
async def sign_document_page(request: Request, doc_type: str, db: AsyncSession = Depends(get_db)):
    """Page for a member to sign NDA or waiver."""
    user = request.session.get("user", {})

    if doc_type not in ("nda", "general_waiver"):
        raise HTTPException(status_code=400, detail="Invalid document type")

    # Get the document content
    doc_content = NDA_TEXT if doc_type == "nda" else WAIVER_TEXT

    return templates.TemplateResponse("pages/sign_document.html", {
        "request": request,
        "user": user,
        "doc_type": doc_type,
        "doc_title": "Non-Disclosure Agreement" if doc_type == "nda" else "General Waiver & Release of Liability",
        "doc_content": doc_content,
    })


@router.post("/documents/sign/{doc_type}")
@require_auth
async def submit_signature(request: Request, doc_type: str, db: AsyncSession = Depends(get_db)):
    """Process a digital signature submission."""
    user = request.session.get("user", {})

    if doc_type not in ("nda", "general_waiver"):
        raise HTTPException(status_code=400, detail="Invalid document type")

    form = await request.form()
    full_name = form.get("full_name", "").strip()
    signature = form.get("signature", "").strip()
    agree = form.get("agree")

    if not full_name or not signature or not agree:
        raise HTTPException(status_code=400, detail="All fields are required")

    # Get member record
    nc_username = user.get("username", user.get("uid", ""))
    result = await db.execute(select(Member).where(Member.nc_username == nc_username))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Get IP from request
    ip_addr = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    ua = request.headers.get("user-agent", "")

    # Record signature
    sig = DocumentSignature(
        member_id=member.id,
        document_type=doc_type,
        full_name=full_name,
        signature_text=signature,
        ip_address=ip_addr,
        user_agent=ua,
    )
    db.add(sig)

    # Update member record
    if doc_type == "nda":
        member.nda_signed_at = datetime.utcnow()
        member.nda_ip_address = ip_addr
    else:
        member.waiver_signed_at = datetime.utcnow()
        member.waiver_ip_address = ip_addr

    await db.commit()

    # Archive signed doc receipt to NC: Personnel/{LastName, FirstName}/Docs/
    try:
        doc_label = "NDA" if doc_type == "nda" else "General_Waiver"
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        filename = f"{doc_label}_signed_{date_str}.txt"

        receipt = (
            f"{'Non-Disclosure Agreement' if doc_type == 'nda' else 'General Waiver & Release of Liability'}\n"
            f"{'=' * 60}\n\n"
            f"Signed by: {full_name}\n"
            f"Digital signature: {signature}\n"
            f"Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"IP address: {ip_addr}\n"
            f"User agent: {ua}\n"
            f"Member: {member.first_name} {member.last_name} ({member.nc_username})\n"
            f"Serial: {member.serial_number or 'N/A'}\n"
        )

        member_folder = f"{member.last_name}, {member.first_name}"
        nc_base = "/remote.php/dav/files/spooky/13th%20Legion%20Shared/%5bS-1%5d%20Admin/Personnel"
        nc_user_arch = NC_SPOOKY_USER
        nc_pass_arch = NC_SPOOKY_PASS

        async with httpx.AsyncClient(timeout=10) as client:
            # Ensure folders exist
            for folder in [member_folder, f"{member_folder}/Docs"]:
                parts = folder.split("/")
                path = f"{NC_URL}{nc_base}"
                for part in parts:
                    path = f"{path}/{quote(part)}"
                    await client.request("MKCOL", f"{path}/", auth=(nc_user_arch, nc_pass_arch))

            # Upload receipt
            upload_path = f"{NC_URL}{nc_base}/{quote(member_folder)}/Docs/{quote(filename)}"
            resp = await client.put(upload_path, content=receipt.encode(), auth=(nc_user_arch, nc_pass_arch))
            if resp.status_code in (201, 204):
                logger.info(f"Archived {doc_label} signing receipt for {member.last_name} to NC")
            else:
                logger.warning(f"NC upload returned {resp.status_code} for {doc_label} receipt")
    except Exception as e:
        logger.error(f"Failed to archive {doc_type} receipt to NC: {e}")

    return templates.TemplateResponse("pages/document_signed.html", {
        "request": request,
        "user": user,
        "doc_type": doc_type,
        "doc_title": "Non-Disclosure Agreement" if doc_type == "nda" else "General Waiver & Release of Liability",
    })


@router.get("/documents/status")
@require_auth
async def document_status(request: Request, db: AsyncSession = Depends(get_db)):
    """S1 view — who has signed what."""
    user = request.session.get("user", {})
    require_s1(user)

    result = await db.execute(
        select(Member)
        .where(Member.status.in_(["recruit", "active"]))
        .order_by(Member.last_name)
    )
    members = result.scalars().all()

    return templates.TemplateResponse("pages/s1_documents.html", {
        "request": request,
        "user": user,
        "members": members,
    })


# ─── PP-022: Recruiter Auto-Assignment ──────────────────────────────────────

# Deck board & stack IDs for S1 Recruit Pipeline
DECK_BOARD_ID = 5
DECK_STACKS = {
    11: "New Application",
    12: "Background Check",
    13: "Interview",
    14: "Documents & Payment",
    15: "Approved — Onboarding",
    16: "Complete",
}
# Stacks that represent active applicants (exclude Complete)
DECK_ACTIVE_STACKS = {11, 12, 13, 14, 15}

# Stage flow: current_stack_id → next_stack_id
STAGE_FLOW = {
    11: 12,  # New Application → Background Check
    12: 13,  # Background Check → Interview
    13: 14,  # Interview → Documents & Payment
    14: 15,  # Documents & Payment → Approved — Onboarding
    15: 16,  # Approved — Onboarding → Complete
}

# Labels for the "advance" button per stage
ADVANCE_LABELS = {
    11: "Start Background Check",
    12: "Schedule Interview",
    13: "Move to Docs & Payment",
    14: "✅ Approve — Begin Onboarding",
    15: "Mark Complete",
}


async def _fetch_pipeline_applicants() -> list[dict]:
    """Fetch applicants from the S1 Recruit Pipeline Deck board."""
    url = f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks"
    applicants = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()
            stacks = resp.json()

            for stack in stacks:
                stack_id = stack.get("id")
                if stack_id not in DECK_ACTIVE_STACKS:
                    continue
                stack_name = DECK_STACKS.get(stack_id, stack.get("title", "Unknown"))
                for card in stack.get("cards", []):
                    # Clean up card title (remove ✅ prefix etc.)
                    title = card.get("title", "").strip().lstrip("✅📋🔍").strip()
                    applicants.append({
                        "id": card["id"],
                        "name": title,
                        "stage": stack_name,
                        "stage_id": stack_id,
                        "created": card.get("createdAt", 0),
                        "assigned": card.get("assignedUsers", []),
                        "advance_label": ADVANCE_LABELS.get(stack_id),
                        "next_stage": DECK_STACKS.get(STAGE_FLOW.get(stack_id, 0), ""),
                        "is_approve": stack_id == 14,  # Docs & Payment → Approve triggers onboarding
                    })
    except Exception:
        pass
    return applicants


from app.constants import RANK_ABBR as RANK_ABBR_S1


@router.get("/recruiters")
@require_auth
async def recruiter_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Manage recruiter roster and assignments."""
    user = request.session.get("user", {})
    require_s1(user)

    result = await db.execute(select(Recruiter).order_by(Recruiter.display_name))
    recruiters_raw = result.scalars().all()

    # Enrich recruiters with roster data
    recruiters = []
    for r in recruiters_raw:
        m_result = await db.execute(select(Member).where(Member.nc_username == r.nc_username))
        m = m_result.scalar_one_or_none()
        r.rank_display = RANK_ABBR_S1.get(m.rank_grade, "") if m else ""
        r.member_name = m.last_name if m else r.display_name
        r.callsign = m.callsign if m else None
        recruiters.append(r)

    # Get roster members for the dropdown (active + recruit, exclude existing recruiters)
    existing_usernames = {r.nc_username for r in recruiters_raw}
    roster_result = await db.execute(
        select(Member)
        .where(Member.status.in_(["active", "recruit"]), Member.nc_username.isnot(None))
        .order_by(Member.last_name)
    )
    roster_members = []
    for m in roster_result.scalars().all():
        if m.nc_username not in existing_usernames:
            m.rank_display = RANK_ABBR_S1.get(m.rank_grade, "")
            roster_members.append(m)

    # Fetch applicants from Deck pipeline (not portal DB recruits)
    applicants = await _fetch_pipeline_applicants()

    return templates.TemplateResponse("pages/s1_recruiters.html", {
        "request": request,
        "user": user,
        "recruiters": recruiters,
        "roster_members": roster_members,
        "applicants": applicants,
    })


# ─── PP-051: Pipeline Kanban Dashboard ───────────────────────────────────────

async def _fetch_full_pipeline() -> dict:
    """Fetch all pipeline stacks with cards, including metadata."""
    url = f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks"
    columns = {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()
            stacks = resp.json()

            for stack in stacks:
                stack_id = stack.get("id")
                if stack_id not in DECK_STACKS:
                    continue
                cards = []
                for card in stack.get("cards", []):
                    if card.get("archived"):
                        continue
                    title = card.get("title", "").strip().lstrip("✅📋🔍").strip()

                    # Calculate days in stage from lastModified
                    days_in_stage = 0
                    try:
                        last_mod = card.get("lastModified", 0)
                        if last_mod:
                            from datetime import datetime as _dt, timezone
                            mod_dt = _dt.fromtimestamp(last_mod, tz=timezone.utc)
                            days_in_stage = (_dt.now(timezone.utc) - mod_dt).days
                    except Exception:
                        pass

                    assigned = []
                    for au in card.get("assignedUsers", []):
                        p = au.get("participant", {})
                        assigned.append(p.get("displayname", p.get("uid", "?")))

                    # Extract Proton Mail from description
                    import re as _re
                    proton_email = ""
                    desc = card.get("description", "")
                    pm_match = _re.search(r'\*\*📧 Proton Mail:\*\*\s*\*(.+?)\*', desc)
                    if pm_match:
                        proton_email = pm_match.group(1).strip()
                    has_proton = bool(proton_email and "pending" not in proton_email.lower())

                    # Extract payment status from description
                    payment_status = "pending"
                    payment_method = ""
                    pay_match = _re.search(r'\*\*💰 Payment:\*\*\s*\*(.+?)\*', desc)
                    if pay_match:
                        pay_text = pay_match.group(1).strip()
                        if "waived" in pay_text.lower():
                            payment_status = "waived"
                        elif "paid" in pay_text.lower():
                            payment_status = "paid"
                            payment_method = pay_text

                    cards.append({
                        "id": card["id"],
                        "name": title,
                        "days": days_in_stage,
                        "assigned": assigned,
                        "advance_label": ADVANCE_LABELS.get(stack_id),
                        "is_approve": stack_id == 14,
                        "has_next": stack_id in STAGE_FLOW,
                        "proton_email": proton_email if has_proton else "",
                        "payment_status": payment_status,
                        "payment_method": payment_method,
                        "stack_id": stack_id,
                    })

                # Auto-delete completed cards older than 10 days
                if stack_id == 16:
                    stale = [c for c in cards if c["days"] >= 10]
                    for stale_card in stale:
                        try:
                            await client.delete(
                                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/16/cards/{stale_card['id']}",
                                headers={"OCS-APIRequest": "true"},
                                auth=(NC_SVC_USER, NC_SVC_PASS),
                                timeout=10,
                            )
                            logger.info(f"Auto-deleted completed pipeline card: {stale_card['name']} ({stale_card['days']}d old)")
                        except Exception as e:
                            logger.error(f"Failed to auto-delete pipeline card {stale_card['id']}: {e}")
                    cards = [c for c in cards if c["days"] < 10]

                columns[stack_id] = {
                    "name": DECK_STACKS[stack_id],
                    "cards": cards,
                    "count": len(cards),
                }
    except Exception:
        pass
    return columns


@router.get("/pipeline")
@require_auth
async def pipeline_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Kanban-style pipeline dashboard."""
    user = request.session.get("user", {})
    require_pipeline(user)

    columns = await _fetch_full_pipeline()

    result = await db.execute(select(Recruiter).where(Recruiter.is_active == True).order_by(Recruiter.display_name))
    recruiters = result.scalars().all()

    return templates.TemplateResponse("pages/s1_pipeline.html", {
        "request": request,
        "user": user,
        "columns": columns,
        "stack_order": [11, 12, 13, 14, 15, 16],
        "recruiters": recruiters,
    })


@router.get("/pipeline/board")
@require_auth
async def pipeline_board_partial(request: Request, db: AsyncSession = Depends(get_db)):
    """Return just the kanban board columns (HTMX partial refresh)."""
    user = request.session.get("user", {})
    require_pipeline(user)

    columns = await _fetch_full_pipeline()

    result = await db.execute(select(Recruiter).where(Recruiter.is_active == True).order_by(Recruiter.display_name))
    recruiters = result.scalars().all()

    return templates.TemplateResponse("partials/pipeline_board.html", {
        "request": request,
        "user": user,
        "columns": columns,
        "stack_order": [11, 12, 13, 14, 15, 16],
        "recruiters": recruiters,
    })


@router.post("/recruiters/add")
@require_auth
async def add_recruiter(request: Request, db: AsyncSession = Depends(get_db)):
    """Add a recruiter to the roster."""
    user = request.session.get("user", {})
    require_s1(user)

    form = await request.form()
    member_id = int(form.get("member_id", 0))
    max_load = int(form.get("max_load", 5))

    if not member_id:
        raise HTTPException(status_code=400, detail="Select a member")

    # Look up member from roster
    m_result = await db.execute(select(Member).where(Member.id == member_id))
    member = m_result.scalar_one_or_none()
    if not member or not member.nc_username:
        raise HTTPException(status_code=400, detail="Member not found or has no NC account")

    # Check if already a recruiter
    existing = await db.execute(select(Recruiter).where(Recruiter.nc_username == member.nc_username))
    if existing.scalar_one_or_none():
        return HTMLResponse('<p style="color: #ef6c00; font-weight: 600;">⚠️ Already a recruiter</p>')

    rank = RANK_ABBR_S1.get(member.rank_grade, "")
    display = f"{rank} {member.last_name}".strip()

    recruiter = Recruiter(
        nc_username=member.nc_username,
        display_name=display,
        max_load=max_load,
    )
    db.add(recruiter)
    await db.commit()

    return HTMLResponse(f'<p style="color: #2e7d32; font-weight: 600;">✅ {display} added as recruiter</p>')


@router.post("/recruiters/assign/{member_id}")
@require_auth
async def assign_recruiter(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Auto-assign or manually assign a recruiter to a recruit."""
    user = request.session.get("user", {})
    require_s1(user)

    form = await request.form()
    manual_recruiter = form.get("recruiter_username", "").strip()

    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    if manual_recruiter:
        # Manual assignment
        recruiter_username = manual_recruiter
    else:
        # Auto-assign: pick recruiter with lowest current load
        result2 = await db.execute(
            select(Recruiter)
            .where(Recruiter.is_active == True, Recruiter.current_load < Recruiter.max_load)
            .order_by(Recruiter.current_load)
            .limit(1)
        )
        recruiter = result2.scalar_one_or_none()
        if not recruiter:
            raise HTTPException(status_code=409, detail="No available recruiters — all at max load")
        recruiter_username = recruiter.nc_username
        recruiter.current_load += 1

    member.assigned_recruiter = recruiter_username
    member.recruiter_assigned_at = datetime.utcnow()
    await db.commit()

    return HTMLResponse(f'<span style="color: #2e7d32; font-weight: 600;">✅ Assigned to {recruiter_username}</span>')


# ─── Welcome Email on Pipeline Completion ────────────────────────────────────

async def _send_welcome_email(card_title: str, card_desc: str) -> str:
    """Send welcome email when a recruit completes the pipeline.

    Extracts Proton email from card description, matches to member record,
    resets password, and sends credentials.

    Returns HTML status snippet for the advance response.
    """
    import re as _re
    import secrets
    import string

    # Extract Proton email from card description
    pm_match = _re.search(r'\*\*📧 Proton Mail:\*\*\s*\*(.+?)\*', card_desc)
    if not pm_match:
        logger.warning(f"Welcome email skipped — no Proton email on card: {card_title}")
        return '<span style="color:#ef6c00;font-size:11px;"> ⚠️ No Proton email on card</span>'

    proton_email = pm_match.group(1).strip()
    if not proton_email or "pending" in proton_email.lower():
        logger.warning(f"Welcome email skipped — Proton email pending: {card_title}")
        return '<span style="color:#ef6c00;font-size:11px;"> ⚠️ Proton email pending</span>'

    # Extract name from card title (strip emoji prefixes)
    name = card_title.strip().lstrip("✅📋🔍").strip()
    name_parts = name.split(None, 1)
    first_name = name_parts[0] if name_parts else name

    # Try to match to a member record by name
    try:
        from sqlalchemy import select as _select
        from app.database import async_session
        from app.models.member import Member

        async with async_session() as db:
            if len(name_parts) >= 2:
                result = await db.execute(
                    _select(Member).where(
                        Member.first_name == name_parts[0],
                        Member.last_name == name_parts[1],
                    )
                )
            else:
                result = await db.execute(
                    _select(Member).where(Member.last_name == name)
                )
            member = result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Welcome email — DB lookup failed for {name}: {e}")
        member = None

    nc_username = member.nc_username if member else None

    # Generate a temp password and reset via NC API if we have a username
    temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))
    if nc_username:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.put(
                    f"{NC_URL}/ocs/v2.php/cloud/users/{nc_username}",
                    auth=(NC_SVC_USER, NC_SVC_PASS),
                    headers={"OCS-APIRequest": "true"},
                    data={"key": "password", "value": temp_password},
                )
                if resp.status_code != 200:
                    logger.error(f"NC password reset failed for {nc_username}: {resp.status_code}")
                    temp_password = "(password not reset — contact S6)"
        except Exception as e:
            logger.error(f"NC password reset error for {nc_username}: {e}")
            temp_password = "(password not reset — contact S6)"

    # Build and send the email
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    subject = "13th Legion — Your Nextcloud & Portal Access"

    html_body = f"""<div style="font-family:sans-serif;max-width:600px;">
    <h2 style="color:#d4a537;">Welcome to the 13th Legion</h2>
    <p>Welcome to the 13th Legion digital infrastructure, {first_name}!</p>
    <p>Your Nextcloud account is ready. This is where we manage files, calendars, tasks, and comms for the unit.</p>

    <div style="background:#f5f5f5;padding:16px;border-radius:8px;margin:16px 0;">
        <p style="margin:4px 0;"><strong>Nextcloud:</strong> <a href="https://cloud.13thlegion.org">cloud.13thlegion.org</a></p>
        {f'<p style="margin:4px 0;"><strong>Username:</strong> <code>{nc_username}</code></p>' if nc_username else ''}
        <p style="margin:4px 0;"><strong>Temporary Password:</strong> <code>{temp_password}</code></p>
        <p style="margin:4px 0;"><strong>Portal:</strong> <a href="https://portal.13thlegion.org">portal.13thlegion.org</a></p>
        <p style="margin:4px 0;font-size:12px;color:#666;">(Portal uses the same Nextcloud login)</p>
    </div>

    <p><strong>First steps:</strong></p>
    <ol>
        <li>Log in to Nextcloud and <strong>change your password</strong> (Settings → Security)</li>
        <li>Set up <strong>2FA</strong> (Settings → Security → TOTP)</li>
        <li>Install the <strong>Nextcloud app</strong> on your phone for notifications</li>
        <li>Log in to the <strong>Portal</strong> to see your profile, training record, and upcoming events</li>
    </ol>

    <p>If you have any issues, reach out to Cav or Archer.</p>
    <p>V/R,<br>13th Legion S6</p>
</div>"""

    text_body = f"""Welcome to the 13th Legion, {first_name}!

Your Nextcloud account is ready.

Nextcloud: https://cloud.13thlegion.org
{f"Username: {nc_username}" if nc_username else ""}
Temporary Password: {temp_password}
Portal: https://portal.13thlegion.org (same login)

First steps:
1. Log in to Nextcloud and change your password (Settings > Security)
2. Set up 2FA (Settings > Security > TOTP)
3. Install the Nextcloud app on your phone
4. Log in to the Portal to see your profile and training record

Questions? Contact admin@13thlegion.org

V/R,
13th Legion S6"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = proton_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [proton_email], msg.as_string())
        logger.info(f"Welcome email sent to {proton_email} for {name}")
        return f'<span style="color:#2e7d32;font-size:11px;"> 📧 Welcome email → {proton_email}</span>'
    except Exception as e:
        logger.error(f"Welcome email failed for {proton_email}: {e}")
        return f'<span style="color:#c62828;font-size:11px;"> ⚠️ Email failed: {e}</span>'


# ─── PP-046: Pipeline Stage Transitions ─────────────────────────────────────

@router.post("/pipeline/{card_id}/advance")
@require_auth
async def advance_pipeline_stage(request: Request, card_id: int):
    """Move a Deck card to the next pipeline stage."""
    user = request.session.get("user", {})
    require_pipeline(user)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # First, find the card's current stack
            current_stack_id = None
            resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()

            card_title = "Unknown"
            for stack in resp.json():
                for card in stack.get("cards", []):
                    if card["id"] == card_id:
                        current_stack_id = stack["id"]
                        card_title = card.get("title", "Unknown")
                        card_desc = card.get("description", "")
                        break
                if current_stack_id:
                    break

            if not current_stack_id:
                return HTMLResponse('<span style="color:#c62828;">❌ Card not found</span>')

            next_stack_id = STAGE_FLOW.get(current_stack_id)
            if not next_stack_id:
                return HTMLResponse('<span style="color:#888;">Already at final stage</span>')

            # Move the card — PUT requires title, description, owner
            move_resp = await client.put(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/{next_stack_id}/cards/{card_id}",
                headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                json={
                    "title": card_title,
                    "description": card_desc,
                    "type": "plain",
                    "order": 999,
                    "owner": NC_SVC_USER,
                },
            )

            if move_resp.status_code in (200, 201):
                next_name = DECK_STACKS.get(next_stack_id, "next stage")
                by = user.get("display_name", user.get("uid", "unknown"))

                # Add a comment to the card noting the transition
                await client.post(
                    f"{NC_URL}/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments",
                    headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                    auth=(NC_SVC_USER, NC_SVC_PASS),
                    json={"message": f"📋 Moved to **{next_name}** by {by} via Portal"},
                )

                # Auto-send welcome email when moving to Approved — Onboarding
                welcome_status = ""
                if next_stack_id == 15:
                    welcome_status = await _send_welcome_email(card_title, card_desc)

                return HTMLResponse(
                    f'<div style="display:flex;align-items:center;gap:8px;">'
                    f'<span style="color:#2e7d32;font-weight:600;">✅ → {next_name}</span>'
                    f'{welcome_status}'
                    f'<span style="color:#888;font-size:11px;">by {by}</span>'
                    f'</div>',
                    headers={"HX-Trigger": "pipelineChanged"},
                )
            else:
                return HTMLResponse(f'<span style="color:#c62828;">❌ Move failed ({move_resp.status_code})</span>')

    except Exception as e:
        return HTMLResponse(f'<span style="color:#c62828;">❌ Error: {e}</span>')


@router.post("/pipeline/{card_id}/decline")
@require_auth
async def decline_applicant(request: Request, card_id: int):
    """Decline/reject an applicant — archives the Deck card."""
    user = request.session.get("user", {})
    require_pipeline(user)

    form = await request.form()
    reason = form.get("reason", "other")

    reason_labels = {
        "failed_bg": "Failed background check",
        "no_show": "No-show interview",
        "withdrew": "Applicant withdrew",
        "ineligible": "Ineligible",
        "other": "Other",
    }
    reason_text = reason_labels.get(reason, reason)
    by = user.get("display_name", user.get("uid", "unknown"))

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Add decline comment
            await client.post(
                f"{NC_URL}/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments",
                headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                json={"message": f"❌ **DECLINED** — {reason_text}\nBy: {by}"},
            )

            # Archive the card (set to archived)
            # Deck API: PUT with archived flag
            # First get card details
            stacks_resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            stacks_resp.raise_for_status()

            card_data = None
            card_stack = None
            for stack in stacks_resp.json():
                for card in stack.get("cards", []):
                    if card["id"] == card_id:
                        card_data = card
                        card_stack = stack["id"]
                        break
                if card_data:
                    break

            if card_data:
                # Archive by setting archived = true
                archive_resp = await client.put(
                    f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/{card_stack}/cards/{card_id}",
                    headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                    auth=(NC_SVC_USER, NC_SVC_PASS),
                    json={
                        "title": f"❌ {card_data['title']}",
                        "description": card_data.get("description", ""),
                        "type": "plain",
                        "order": card_data.get("order", 0),
                        "owner": NC_SVC_USER,
                        "archived": True,
                    },
                )

            return HTMLResponse(
                f'<div style="padding:8px;background:rgba(198,40,40,0.1);border-radius:4px;">'
                f'<span style="color:#c62828;font-weight:600;">❌ Declined — {reason_text}</span>'
                f'<span style="color:#888;font-size:11px;margin-left:8px;">by {by}</span>'
                f'</div>',
                headers={"HX-Trigger": "pipelineChanged"},
            )

    except Exception as e:
        return HTMLResponse(f'<span style="color:#c62828;">❌ Error: {e}</span>')


# ─── PP-048: Card Notes & Comments ───────────────────────────────────────────

@router.get("/pipeline/{card_id}/comments")
@require_auth
async def get_card_comments(request: Request, card_id: int):
    """Fetch comments for a Deck card."""
    user = request.session.get("user", {})
    require_pipeline(user)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{NC_URL}/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()
            data = resp.json()
            comments = data.get("ocs", {}).get("data", [])

        if not comments:
            return HTMLResponse('<p style="color:#888;font-size:12px;padding:4px;">No notes yet.</p>')

        html_parts = []
        for c in comments:
            author = c.get("actorDisplayName", c.get("actorId", "Unknown"))
            message = c.get("message", "").replace("\n", "<br>")
            created = c.get("creationDateTime", "")
            # Parse ISO datetime
            try:
                from datetime import datetime as _dt
                dt = _dt.fromisoformat(created.replace("Z", "+00:00"))
                time_str = dt.strftime("%b %d, %I:%M %p")
            except Exception:
                time_str = created[:16] if created else ""

            html_parts.append(f"""
            <div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:12px;">
                <div style="color:#d4a537;font-weight:600;">{author} <span style="color:#666;font-weight:400;">{time_str}</span></div>
                <div style="color:#ccc;margin-top:2px;line-height:1.4;">{message}</div>
            </div>""")

        return HTMLResponse("".join(html_parts))

    except Exception as e:
        return HTMLResponse(f'<p style="color:#c62828;font-size:12px;">Error loading comments: {e}</p>')


@router.get("/pipeline/{card_id}/details")
@require_auth
async def get_card_details(request: Request, card_id: int):
    """Fetch the Deck card description (form submission data)."""
    user = request.session.get("user", {})
    require_pipeline(user)

    try:
        # Search all stacks for this card
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()
            stacks = resp.json()

            description = ""
            card_title = ""
            for stack in stacks:
                for card in stack.get("cards", []):
                    if card["id"] == card_id:
                        description = card.get("description", "")
                        card_title = card.get("title", "")
                        break

        if not description:
            return HTMLResponse('<p style="color:#888;font-size:12px;">No application data found.</p>')

        # Convert markdown-style bold to HTML, preserve line breaks
        import re
        lines = description.split("\n")
        html_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("---"):
                html_lines.append('<hr style="border:none;border-top:1px solid rgba(255,255,255,0.1);margin:8px 0;">')
                continue
            if line.startswith("*") and line.endswith("*") and not line.startswith("**"):
                # Italic line (usually suggestions)
                html_lines.append(f'<div style="font-size:11px;color:#888;font-style:italic;">{line.strip("*")}</div>')
                continue
            # Convert **Label:** Value to styled row
            bold_match = re.match(r'\*\*(.+?)\*\*\s*(.*)', line)
            if bold_match:
                label = bold_match.group(1)
                value = bold_match.group(2).lstrip(': ').strip()
                if value:
                    html_lines.append(
                        f'<div style="display:flex;gap:6px;padding:3px 0;font-size:12px;">'
                        f'<span style="color:#d4a537;font-weight:600;min-width:120px;flex-shrink:0;">{label}</span>'
                        f'<span style="color:#eee;">{value}</span></div>'
                    )
                else:
                    html_lines.append(f'<div style="font-size:12px;color:#d4a537;font-weight:600;padding:3px 0;">{label}</div>')
            else:
                html_lines.append(f'<div style="font-size:12px;color:#ccc;padding:2px 0;">{line}</div>')

        html = (
            '<div style="max-height:350px;overflow-y:auto;">'
            + "\n".join(html_lines)
            + '</div>'
        )
        return HTMLResponse(html)

    except Exception as e:
        return HTMLResponse(f'<p style="color:#c62828;font-size:12px;">Error: {e}</p>')


@router.post("/pipeline/{card_id}/protonmail")
@require_auth
async def set_protonmail(request: Request, card_id: int):
    """Update the Proton Mail field in a Deck card description."""
    import re
    user = request.session.get("user", {})
    require_pipeline(user)

    form = await request.form()
    proton_email = form.get("proton_email", "").strip()
    if not proton_email:
        return HTMLResponse('<p style="color:#c62828;font-size:11px;">Email required</p>')

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Find the card across stacks
            resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()
            stacks = resp.json()

            card_data = None
            stack_id = None
            for stack in stacks:
                for card in stack.get("cards", []):
                    if card["id"] == card_id:
                        card_data = card
                        stack_id = stack["id"]
                        break
                if card_data:
                    break

            if not card_data:
                return HTMLResponse('<p style="color:#c62828;font-size:11px;">Card not found</p>')

            desc = card_data.get("description", "")

            # Replace the Proton Mail line (pending or existing)
            proton_line = f"**📧 Proton Mail:** *{proton_email}*"
            if re.search(r'\*\*📧 Proton Mail:\*\*', desc):
                desc = re.sub(
                    r'\*\*📧 Proton Mail:\*\*.*',
                    proton_line,
                    desc
                )
            else:
                # Append before the suggestion line or at end
                desc = desc.rstrip() + f"\n\n{proton_line}"

            # Update card — owner must be a plain UID string, not the full object
            owner = card_data.get("owner")
            if isinstance(owner, dict):
                owner = owner.get("uid") or owner.get("primaryKey") or "portal-svc"
            elif not owner:
                owner = "portal-svc"

            resp2 = await client.put(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/{stack_id}/cards/{card_id}",
                headers={"OCS-APIRequest": "true", "Accept": "application/json", "Content-Type": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                json={
                    "title": card_data["title"],
                    "type": card_data.get("type", "plain"),
                    "description": desc,
                    "owner": owner,
                },
            )
            resp2.raise_for_status()

        return HTMLResponse(f'<p style="color:#2e7d32;font-size:11px;font-weight:600;">✅ {proton_email}</p>')
    except Exception as e:
        return HTMLResponse(f'<p style="color:#c62828;font-size:11px;">Error: {e}</p>')


@router.post("/pipeline/{card_id}/payment")
@require_auth
async def set_card_payment(request: Request, card_id: int):
    """Update payment status on a Deck card description."""
    import re
    user = request.session.get("user", {})
    require_pipeline(user)

    form = await request.form()
    status = form.get("status", "paid")  # paid or waived
    method = form.get("method", "")  # cash, venmo, zelle, paypal

    if status == "paid":
        pay_text = f"Paid — {method}" if method else "Paid"
    else:
        pay_text = "Waived"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()

            card_data = None
            stack_id = None
            for stack in resp.json():
                for card in stack.get("cards", []):
                    if card["id"] == card_id:
                        card_data = card
                        stack_id = stack["id"]
                        break
                if card_data:
                    break

            if not card_data:
                return HTMLResponse('<p style="color:#c62828;font-size:11px;">Card not found</p>')

            desc = card_data.get("description", "")
            payment_line = f"**💰 Payment:** *{pay_text}*"

            if re.search(r'\*\*💰 Payment:\*\*', desc):
                desc = re.sub(r'\*\*💰 Payment:\*\*.*', payment_line, desc)
            else:
                # Insert after Proton Mail line or at end
                if '📧 Proton Mail:' in desc:
                    desc = re.sub(
                        r'(\*\*📧 Proton Mail:\*\*[^\n]*)',
                        rf'\1\n{payment_line}',
                        desc
                    )
                else:
                    desc = desc.rstrip() + f"\n\n{payment_line}"

            owner = card_data.get("owner")
            if isinstance(owner, dict):
                owner = owner.get("uid") or owner.get("primaryKey") or "portal-svc"
            elif not owner:
                owner = "portal-svc"

            resp2 = await client.put(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/{stack_id}/cards/{card_id}",
                headers={"OCS-APIRequest": "true", "Accept": "application/json", "Content-Type": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                json={
                    "title": card_data["title"],
                    "type": card_data.get("type", "plain"),
                    "description": desc,
                    "owner": owner,
                },
            )
            resp2.raise_for_status()

            by = user.get("display_name", user.get("uid", "unknown"))
            await client.post(
                f"{NC_URL}/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments",
                headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                json={"message": f"💰 Payment marked: **{pay_text}** by {by}"},
            )

        color = '#2e7d32' if status == 'paid' else '#1565c0'
        icon = '✅' if status == 'paid' else '🔵'
        return HTMLResponse(
            f'<span style="font-size:11px;color:{color};font-weight:600;">{icon} {pay_text}</span>',
            headers={"HX-Trigger": "pipelineChanged"},
        )
    except Exception as e:
        return HTMLResponse(f'<p style="color:#c62828;font-size:11px;">Error: {e}</p>')


@router.post("/pipeline/{card_id}/comment")
@require_auth
async def add_card_comment(request: Request, card_id: int):
    """Add a comment to a Deck card."""
    user = request.session.get("user", {})
    require_pipeline(user)

    form = await request.form()
    message = form.get("message", "").strip()
    if not message:
        return HTMLResponse('<span style="color:#c62828;">Empty note</span>')

    by = user.get("display_name", user.get("uid", "unknown"))

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{NC_URL}/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments",
                headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                json={"message": f"[{by}] {message}"},
            )

        if resp.status_code in (200, 201):
            return HTMLResponse(f'<span style="color:#2e7d32;font-size:12px;">✅ Note added</span>')
        else:
            return HTMLResponse(f'<span style="color:#c62828;font-size:12px;">Failed ({resp.status_code})</span>')

    except Exception as e:
        return HTMLResponse(f'<span style="color:#c62828;font-size:12px;">Error: {e}</span>')


# ─── PP-049: File Attachments Viewer ─────────────────────────────────────────

@router.get("/pipeline/{card_id}/attachments")
@require_auth
async def get_card_attachments(request: Request, card_id: int):
    """Fetch attachments for a Deck card."""
    user = request.session.get("user", {})
    require_pipeline(user)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Find which stack the card is in
            stacks_resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            stacks_resp.raise_for_status()

            stack_id = None
            for stack in stacks_resp.json():
                for card in stack.get("cards", []):
                    if card["id"] == card_id:
                        stack_id = stack["id"]
                        break
                if stack_id:
                    break

            if not stack_id:
                return HTMLResponse('<p style="color:#888;font-size:12px;">Card not found.</p>')

            resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/{stack_id}/cards/{card_id}/attachments",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()
            attachments = resp.json()

        if not attachments:
            return HTMLResponse('<p style="color:#888;font-size:12px;">No attachments.</p>')

        html_parts = []
        for att in attachments:
            name = att.get("data", att.get("id", "file"))
            att_id = att.get("id")
            ext = name.rsplit(".", 1)[-1].lower() if "." in str(name) else ""
            icon = "📄"
            if ext in ("pdf",):
                icon = "📕"
            elif ext in ("jpg", "jpeg", "png", "gif", "webp"):
                icon = "🖼️"
            elif ext in ("doc", "docx"):
                icon = "📝"

            dl_url = f"/api/s1/pipeline/{card_id}/attachments/{att_id}/download"

            html_parts.append(f"""
            <div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px;">
                <span>{icon}</span>
                <a href="{dl_url}" target="_blank" style="color:#d4a537;text-decoration:none;">{name}</a>
            </div>""")

        return HTMLResponse("".join(html_parts))

    except Exception as e:
        return HTMLResponse(f'<p style="color:#c62828;font-size:12px;">Error: {e}</p>')


@router.get("/pipeline/{card_id}/attachments/{attachment_id}/download")
@require_auth
async def download_attachment(request: Request, card_id: int, attachment_id: int):
    """Proxy download of a Deck card attachment."""
    from fastapi.responses import StreamingResponse

    user = request.session.get("user", {})
    require_pipeline(user)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Find stack
            stacks_resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            stack_id = None
            for stack in stacks_resp.json():
                for card in stack.get("cards", []):
                    if card["id"] == card_id:
                        stack_id = stack["id"]
                        break
                if stack_id:
                    break

            if not stack_id:
                raise HTTPException(status_code=404, detail="Card not found")

            resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/{stack_id}/cards/{card_id}/attachments/{attachment_id}",
                headers={"OCS-APIRequest": "true"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "application/octet-stream")
            disposition = resp.headers.get("content-disposition", "")

            return StreamingResponse(
                iter([resp.content]),
                media_type=content_type,
                headers={"Content-Disposition": disposition} if disposition else {},
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── PP-050: Recruiter Assignment from Pipeline ─────────────────────────────

@router.post("/pipeline/{card_id}/assign-recruiter")
@require_auth
async def assign_recruiter_to_card(request: Request, card_id: int, db: AsyncSession = Depends(get_db)):
    """Assign a recruiter to a Deck card (adds as card member) and tracks in DB."""
    user = request.session.get("user", {})
    require_pipeline(user)

    form = await request.form()
    recruiter_username = form.get("recruiter", "").strip()
    if not recruiter_username:
        return HTMLResponse('<span style="color:#c62828;font-size:12px;">No recruiter selected</span>')

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Assign user to the Deck card
            resp = await client.put(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/0/cards/{card_id}/assignUser",
                headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                json={"userId": recruiter_username},
            )

            # Also add a comment
            by = user.get("display_name", user.get("uid", "unknown"))
            await client.post(
                f"{NC_URL}/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments",
                headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                json={"message": f"👤 Recruiter assigned: **{recruiter_username}** by {by}"},
            )

        # Update recruiter load in DB
        result = await db.execute(
            select(Recruiter).where(Recruiter.nc_username == recruiter_username)
        )
        recruiter = result.scalar_one_or_none()
        if recruiter:
            recruiter.current_load += 1
            await db.commit()

        return HTMLResponse(f'<span style="color:#2e7d32;font-size:12px;">✅ Assigned to {recruiter_username}</span>')

    except Exception as e:
        return HTMLResponse(f'<span style="color:#c62828;font-size:12px;">Error: {e}</span>')


# ─── PP-024: Offboarding / Separation ────────────────────────────────────────

@router.get("/offboard")
@require_auth
async def offboard_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Offboarding management page."""
    user = request.session.get("user", {})
    require_s1(user)

    # Active members (for initiating separation)
    result = await db.execute(
        select(Member)
        .where(Member.status.in_(["active", "recruit"]))
        .order_by(Member.last_name)
    )
    active_members = result.scalars().all()

    # Recent separations (join member for name display)
    from sqlalchemy.orm import joinedload, relationship
    # Use a manual join since there's no relationship defined
    result2 = await db.execute(
        select(SeparationLog, Member)
        .join(Member, SeparationLog.member_id == Member.id, isouter=True)
        .order_by(desc(SeparationLog.created_at))
        .limit(20)
    )
    recent_separations = []
    for row in result2.all():
        log, member = row[0], row[1]
        if member:
            member.rank_display = RANK_ABBR_S1.get(member.rank_grade, "")
        recent_separations.append({"log": log, "member": member})

    return templates.TemplateResponse("pages/s1_offboard.html", {
        "request": request,
        "user": user,
        "active_members": active_members,
        "recent_separations": recent_separations,
    })


@router.post("/offboard/{member_id}")
@require_auth
async def process_offboarding(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Process a member separation."""
    user = request.session.get("user", {})
    require_s1(user)

    form = await request.form()
    reason = form.get("reason", "voluntary")  # voluntary, involuntary, inactivity, blacklisted
    notes = form.get("notes", "").strip() or None
    disable_nc = form.get("disable_nc") == "on"
    revoke_portal = form.get("revoke_portal") == "on"
    remove_groups = form.get("remove_groups") == "on"

    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    initiated_by = user.get("display_name", user.get("uid", "unknown"))

    # Update member status
    if reason == "blacklisted":
        member.status = "blacklisted"
    else:
        member.status = "separated"
    member.separation_date = datetime.utcnow().date()
    member.separation_reason = reason
    member.separation_notes = notes
    member.separation_initiated_by = initiated_by

    # Log the separation
    log_entry = SeparationLog(
        member_id=member.id,
        reason=reason,
        initiated_by=initiated_by,
        notes=notes,
    )

    # Disable NC account if requested
    if disable_nc and member.nc_username:
        try:
            async with httpx.AsyncClient(auth=(NC_SVC_USER, NC_SVC_PASS)) as client:
                resp = await client.put(
                    f"{NC_URL}/ocs/v2.php/cloud/users/{member.nc_username}/disable",
                    headers={"OCS-APIRequest": "true"},
                    timeout=15,
                )
                log_entry.nc_account_disabled = resp.status_code in (200, 100)
        except Exception:
            log_entry.nc_account_disabled = False

    # Remove from NC groups if requested
    if remove_groups and member.nc_username:
        try:
            async with httpx.AsyncClient(auth=(NC_SVC_USER, NC_SVC_PASS)) as client:
                resp = await client.get(
                    f"{NC_URL}/ocs/v2.php/cloud/users/{member.nc_username}/groups",
                    headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                    timeout=15,
                )
                if resp.status_code == 200:
                    groups = resp.json().get("ocs", {}).get("data", {}).get("groups", [])
                    for group in groups:
                        await client.delete(
                            f"{NC_URL}/ocs/v2.php/cloud/users/{member.nc_username}/groups",
                            headers={"OCS-APIRequest": "true"},
                            data={"groupid": group},
                            timeout=10,
                        )
                    log_entry.groups_removed = True
        except Exception:
            log_entry.groups_removed = False

    log_entry.portal_access_revoked = revoke_portal
    db.add(log_entry)
    await db.commit()

    # Send separation notification email
    email_sent = _send_offboard_email(member, reason, notes)

    return HTMLResponse(f"""
        <div style="padding: 16px; background: #fff3e0; border-left: 4px solid #e65100; border-radius: 4px;">
            <strong>Separated:</strong> {member.first_name} {member.last_name} — {reason}
            {'<br>NC account disabled ✅' if log_entry.nc_account_disabled else ''}
            {'<br>Groups removed ✅' if log_entry.groups_removed else ''}
            {'<br>Separation email sent ✅' if email_sent else '<br>⚠️ No email on file — notification not sent' if not member.email else '<br>⚠️ Email send failed'}
        </div>
    """)


# ─── Reactivation ────────────────────────────────────────────────────────────

@router.post("/reactivate/{member_id}")
@require_auth
async def reactivate_member(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Reactivate a separated/inactive member."""
    user = request.session.get("user", {})
    require_s1(user)

    form = await request.form()
    new_status = form.get("status", "active")  # active or recruit
    notes = form.get("notes", "").strip() or None
    enable_nc = form.get("enable_nc") == "on"
    restore_groups = form.get("restore_groups") == "on"

    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    if member.status not in ("separated", "inactive"):
        raise HTTPException(status_code=400, detail=f"Cannot reactivate member with status '{member.status}'")

    initiated_by = user.get("display_name", user.get("uid", "unknown"))
    old_status = member.status

    # Update member status
    member.status = new_status
    member.separation_date = None
    member.separation_reason = None

    # Log as reactivation in separation log
    log_entry = SeparationLog(
        member_id=member.id,
        reason=f"reactivated ({old_status} → {new_status})",
        initiated_by=initiated_by,
        notes=notes,
    )

    # Re-enable NC account if requested
    nc_enabled = False
    if enable_nc and member.nc_username:
        try:
            async with httpx.AsyncClient(auth=(NC_SVC_USER, NC_SVC_PASS)) as client:
                resp = await client.put(
                    f"{NC_URL}/ocs/v2.php/cloud/users/{member.nc_username}/enable",
                    headers={"OCS-APIRequest": "true"},
                    timeout=15,
                )
                nc_enabled = resp.status_code in (200, 100)
        except Exception:
            nc_enabled = False
        log_entry.nc_account_disabled = not nc_enabled  # False = account is enabled

    # Re-add to NC groups if requested
    groups_restored = False
    if restore_groups and member.nc_username:
        try:
            # Build group list based on member attributes
            groups = ["13th Legion"]
            if new_status == "recruit":
                groups.append("Rank - Recruit")
            elif member.rank_grade:
                grade = member.rank_grade.split("-")[0] if "-" in member.rank_grade else ""
                if grade == "E" and int(member.rank_grade.split("-")[1]) >= 5:
                    groups.append("Rank - NCO")
                elif grade == "O":
                    groups.append("Rank - Officer")
                elif grade == "W":
                    groups.append("Rank - Officer")
                else:
                    groups.append("Rank - Patched")
            if member.team:
                groups.append(f"Team - {member.team}")

            async with httpx.AsyncClient(auth=(NC_SVC_USER, NC_SVC_PASS)) as client:
                for group in groups:
                    await client.post(
                        f"{NC_URL}/ocs/v2.php/cloud/users/{member.nc_username}/groups",
                        headers={"OCS-APIRequest": "true"},
                        data={"groupid": group},
                        timeout=10,
                    )
            groups_restored = True
        except Exception:
            groups_restored = False
        log_entry.groups_removed = not groups_restored  # False = groups are present

    db.add(log_entry)
    await db.commit()

    return HTMLResponse(f"""
        <div style="padding: 16px; background: #e8f5e9; border-left: 4px solid #2e7d32; border-radius: 4px;">
            <strong>✅ Reactivated:</strong> {member.first_name} {member.last_name} → {new_status}
            {'<br>NC account enabled ✅' if nc_enabled else ''}
            {'<br>Groups restored ✅' if groups_restored else ''}
            {f'<br><em>Notes: {notes}</em>' if notes else ''}
        </div>
    """)


# ─── Document texts (abbreviated for now — replace with actual TSM docs) ────

NDA_TEXT = """
<h2 style="text-align:center;color:#d4a537;">TEXAS STATE MILITIA — 13TH LEGION</h2>
<h3 style="text-align:center;">Member Confidentiality Agreement</h3>

<p>I understand that my access to data, information, and records (all hereinafter referred to
as Information) maintained in the manual and records systems of Texas State Militia (all hereinafter
referred to as Information or intelligence) is limited to my need for the Information in the
performance of my job duties.</p>

<p>By my signature below, I affirm that I have been advised of, understand, and agree to the
following terms and conditions of my access to Information contained herein.</p>

<ol>
    <li>I will use my authorized access to information/intelligence only in the performance
    of the responsibilities of my position as a member of the State Organization staff.</li>

    <li>I will comply with all controls established by Texas State Militia regarding the use
    of Information/intelligence maintained within my assigned unit.</li>

    <li>I will avoid disclosure of Information to unauthorized persons without the appropriate
    consent of my commanding officer or those appointed over me. I understand and agree that
    my obligation to avoid such disclosure will continue even after I leave my position within
    Texas State Militia.</li>

    <li>I will exercise care to protect Information against accidental or unauthorized access,
    modifications, disclosures, or destruction.</li>

    <li>When discussing Information with other members in the course of my duties, I will
    exercise care to keep the conversation private and not overheard by others who are not
    authorized to have access to such Information.</li>

    <li>I understand that any violation of this Agreement or other Organizational policies
    related to or deemed necessary to the appropriate release or disclosure of
    Information/intelligence will result at the minimum, immediate termination of my membership
    and affiliation with the organization. Civil liabilities, criminal charges and any other
    course of disciplinary action deemed necessary to rectify the situation may apply.</li>
</ol>

<p>I affirm that I have been given the opportunity to review and understand this confidentiality
agreement and I further affirm that my questions about those policies have been answered to my
satisfaction. I enter this agreement freely as a commitment of my membership without hesitation
or outside influence.</p>
"""

WAIVER_TEXT = """
<h2 style="text-align:center;color:#d4a537;">TEXAS STATE MILITIA — 13TH LEGION</h2>
<h3 style="text-align:center;">General Release of Liability</h3>

<p>This General Release of Liability Waiver ("Waiver") is executed by the undersigned participant
("Participant") in favor of The Texas State Militia and the 13th Legion (collectively referred to
as the "Organizers"), including their members, officers, agents, volunteers, and representatives.
This Waiver is binding upon the Participant, the Participant's heirs, assigns, and legal
representatives.</p>

<h4>1. ASSUMPTION OF RISK</h4>
<p>The Participant acknowledges that participation in events, training exercises, and activities
("Activities") organized or hosted by the Organizers involves inherent risks, including but not
limited to physical injury, psychological injury, permanent disability, paralysis, and death.
The Participant voluntarily and freely assumes all such risks, known and unknown, associated with
these Activities, regardless of the cause, including but not limited to negligence by the
Organizers.</p>

<h4>2. WAIVER AND RELEASE</h4>
<p>In consideration for being permitted to participate in the Activities, the Participant, on
behalf of themselves, their heirs, assigns, and legal representatives, hereby releases, waives,
discharges, and agrees to hold harmless the Organizers from any and all claims, demands, suits,
actions, liabilities, damages, and expenses, including attorneys' fees, arising out of or related
to any injury, death, or loss that may occur during or in connection with the Activities, whether
caused by the negligence of the Organizers or otherwise.</p>

<h4>3. INDEMNIFICATION</h4>
<p>The Participant agrees to indemnify and hold harmless the Organizers from any and all claims,
actions, suits, costs, damages, and expenses, including attorneys' fees, arising out of or in any
way connected to the Participant's involvement in the Activities.</p>

<h4>4. MEDICAL TREATMENT</h4>
<p>The Participant authorizes the Organizers to provide or secure emergency medical treatment as
deemed necessary and agrees to be financially responsible for any costs incurred as a result of
such treatment.</p>

<h4>5. SEVERABILITY</h4>
<p>If any provision of this Waiver is found to be unenforceable or invalid by a court of competent
jurisdiction, the remaining provisions shall remain in full force and effect.</p>

<h4>6. GOVERNING LAW</h4>
<p>This Waiver shall be governed by and construed in accordance with the laws of the State of
Texas, without regard to its conflict of law principles.</p>

<h4>7. ACKNOWLEDGMENT OF UNDERSTANDING</h4>
<p>The Participant acknowledges that they have carefully read this Waiver, fully understand its
contents, and voluntarily agree to its terms. The Participant is aware that by signing this
document, they are waiving substantial legal rights, including the right to sue the Organizers.</p>
"""
