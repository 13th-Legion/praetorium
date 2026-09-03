"""S1 Admin routes — payment, NDA/waiver, recruiter assignment, offboarding."""

import os
from datetime import datetime
from html import escape
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.routes.doc_texts import CODE_OF_CONDUCT_TEXT, BYLAWS_TEXT, ACTIVITY_POLICY_TEXT
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
from app.constants import S1_ROLES, PIPELINE_ROLES, UNIT_COMMS_ROLES

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


def require_unit_comms(user: dict):
    """Unit Comms / newsletter access — open to all of S1, not just the S1 lead."""
    if not (set(user.get("roles", [])) & UNIT_COMMS_ROLES):
        raise HTTPException(status_code=403, detail="S1 access required")


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

    if doc_type not in ("nda", "general_waiver", "code_of_conduct", "bylaws", "activity_policy"):
        raise HTTPException(status_code=400, detail="Invalid document type")

    # Get the document content
    if doc_type == "nda":
        doc_content = NDA_TEXT
        doc_title = "Non-Disclosure Agreement"
    elif doc_type == "general_waiver":
        doc_content = WAIVER_TEXT
        doc_title = "General Waiver & Release of Liability"
    elif doc_type == "code_of_conduct":
        doc_content = CODE_OF_CONDUCT_TEXT
        doc_title = "Code of Conduct"
    elif doc_type == "activity_policy":
        doc_content = ACTIVITY_POLICY_TEXT
        doc_title = "Activity Policy"
    else:
        doc_content = BYLAWS_TEXT
        doc_title = "TSM By-Laws"

    return templates.TemplateResponse("pages/sign_document.html", {
        "request": request,
        "user": user,
        "doc_type": doc_type,
        "doc_title": doc_title,
        "doc_content": doc_content,
    })


@router.post("/documents/sign/{doc_type}")
@require_auth
async def submit_signature(request: Request, doc_type: str, db: AsyncSession = Depends(get_db)):
    """Process a digital signature submission."""
    user = request.session.get("user", {})

    if doc_type not in ("nda", "general_waiver", "code_of_conduct", "bylaws", "activity_policy"):
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
    elif doc_type == "general_waiver":
        member.waiver_signed_at = datetime.utcnow()
        member.waiver_ip_address = ip_addr
    elif doc_type == "code_of_conduct":
        member.code_of_conduct_signed_at = datetime.utcnow()
        member.code_of_conduct_ip_address = ip_addr
    elif doc_type == "activity_policy":
        member.activity_policy_signed_at = datetime.utcnow()
        member.activity_policy_ip_address = ip_addr
    else:
        member.bylaws_signed_at = datetime.utcnow()
        member.bylaws_ip_address = ip_addr

    await db.commit()

    doc_labels_pretty = {"nda": "NDA", "general_waiver": "General Waiver", "code_of_conduct": "Code of Conduct", "bylaws": "By-Laws", "activity_policy": "Activity Policy"}
    doc_title = doc_labels_pretty.get(doc_type, "Document")

    from app.routes.notifications import create_notification
    from app.services import ranks as _ranks
    # Build display name: "SFC Eastman (Dizz)" style
    _rank = _ranks.abbr_map().get(member.rank_grade, "") if member.rank_grade else ""
    _callsign = f" ({member.callsign})" if member.callsign else ""
    _signer_display = f"{_rank} {member.last_name}{_callsign}".strip()
    doc_names = {"nda": "NDA", "general_waiver": "General Waiver", "code_of_conduct": "Code of Conduct", "bylaws": "TSM By-Laws", "activity_policy": "Activity Policy"}
    _doc_name = doc_names.get(doc_type, doc_type)
    # Notify Command + S1 Lead (based on NC group-derived portal_roles)
    from sqlalchemy import select as notif_select, or_
    from app.models.member import Member as NotifMember
    cmd_result = await db.execute(
        notif_select(NotifMember.id).where(
            NotifMember.status.in_(["active"]),
            or_(
                NotifMember.portal_roles.contains('"command"'),
                NotifMember.portal_roles.contains('"s1_lead"'),
            )
        )
    )
    _notified_ids = set()
    for (mid,) in cmd_result:
        if mid not in _notified_ids:
            _notified_ids.add(mid)
            await create_notification(
                db, mid, "document",
                f"📝 {_signer_display} signed {_doc_name}",
                link="/api/s1/documents/status",
                icon="📝"
            )

    # Archive signed doc receipt to NC: Personnel/{LastName, FirstName}/Docs/
    try:
        doc_labels = {"nda": "NDA", "general_waiver": "General_Waiver", "code_of_conduct": "Code_of_Conduct", "bylaws": "By_Laws", "activity_policy": "Activity_Policy"}
        doc_label = doc_labels.get(doc_type, "Document")
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        filename = f"{doc_label}_signed_{date_str}.txt"

        receipt = (
            f"{doc_names.get(doc_type, 'Document').replace('_', ' ')}\n"
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
        "doc_title": doc_labels.get(doc_type, "Document").replace("_", " "),
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
    81: "On Hold",
}
# Stacks that represent active applicants (exclude Complete and On Hold)
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


# ─── PP-052: Recruiter Credit & Load Accounting ─────────────────────────────
# current_load = ACTIVE pipeline assignments only (members with status='recruit'
# assigned to that recruiter). total_recruited = lifetime completed onboardings
# (incremented once when a recruit reaches the Complete stack). Historically
# both were broken: current_load only ever incremented (never decremented) and
# total_recruited was never touched. These helpers fix that going forward and
# provide a one-time recompute to clear the garbage counters.

async def _match_member_by_card_title(db, card_title: str):
    """Match a Deck card title back to a Member row by name.

    Mirrors the name-matching logic in _send_welcome_email so completion
    credit lands on the same member the welcome email targets.
    """
    from sqlalchemy import select as _select
    name = (card_title or "").strip().lstrip("✅📋🔍").strip()
    if not name:
        return None
    name_parts = name.split(None, 1)
    try:
        if len(name_parts) >= 2:
            result = await db.execute(
                _select(Member).where(
                    Member.first_name == name_parts[0],
                    Member.last_name == name_parts[1],
                ).order_by(Member.id)
            )
        else:
            result = await db.execute(
                _select(Member).where(Member.last_name == name).order_by(Member.id)
            )
        # Use .first() (not scalar_one_or_none) — two members can share a name,
        # which would raise MultipleResultsFound and silently drop the credit.
        rows = result.scalars().all()
        if len(rows) > 1:
            logger.warning(
                f"Credit — ambiguous match for '{name}': {len(rows)} members "
                f"(ids={[m.id for m in rows]}); using lowest id {rows[0].id}. "
                f"Verify recruiter credit manually."
            )
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"Credit — member lookup failed for '{name}': {e}")
        return None


async def _credit_recruiter_on_completion(card_title: str) -> str:
    """Persist recruiter credit when a recruit completes onboarding.

    Called when a card is moved into the Complete stack (15 -> 16). Matches the
    card to a Member; if that member has an assigned_recruiter, increments that
    Recruiter.total_recruited by 1 and decrements current_load by 1 (floor 0).
    Idempotency guard: only credits while the member is still status='recruit'
    (i.e. not yet flipped/patched), so re-runs of the same completion don't
    double-count. Returns an HTML status snippet (or '' if nothing to credit).
    """
    from app.database import async_session
    from sqlalchemy import select as _select
    try:
        async with async_session() as db:
            member = await _match_member_by_card_title(db, card_title)
            if not member:
                return ''
            # Idempotency guard (the docstring's promise, previously missing):
            # only credit while the member is still a recruit. A second move
            # into Complete (or a duplicate stack webhook) finds them already
            # flipped/patched and skips, so the recruiter isn't double-credited
            # and current_load isn't decremented twice.
            if (member.status or "").lower() != "recruit":
                logger.info(
                    f"Recruiter credit skipped for {member.first_name} "
                    f"{member.last_name} — status='{member.status}' (not recruit); "
                    f"already credited."
                )
                return ''
            recruiter_username = (member.assigned_recruiter or "").strip()
            if not recruiter_username:
                return ''
            r_result = await db.execute(
                _select(Recruiter).where(Recruiter.nc_username == recruiter_username)
            )
            recruiter = r_result.scalar_one_or_none()
            if not recruiter:
                return ''
            recruiter.total_recruited = (recruiter.total_recruited or 0) + 1
            recruiter.current_load = max(0, (recruiter.current_load or 0) - 1)
            await db.commit()
            logger.info(
                f"Recruiter credit: {recruiter.nc_username} +1 recruited "
                f"(total={recruiter.total_recruited}, load={recruiter.current_load}) "
                f"for {member.first_name} {member.last_name}"
            )
            return (
                f'<span style="color:#2e7d32;font-size:11px;"> 🎖️ Credit → '
                f'{recruiter.display_name}</span>'
            )
    except Exception as e:
        logger.error(f"Recruiter credit failed for card '{card_title}': {e}")
    return ''


async def _release_recruiter_load(db, member) -> None:
    """Decrement a recruiter's current_load when a recruit leaves the active
    pipeline (declined / separated / dropped) without completing. Floors at 0.
    Safe to call even if the member has no assigned recruiter."""
    from sqlalchemy import select as _select
    recruiter_username = (getattr(member, "assigned_recruiter", None) or "").strip()
    if not recruiter_username:
        return
    try:
        r_result = await db.execute(
            _select(Recruiter).where(Recruiter.nc_username == recruiter_username)
        )
        recruiter = r_result.scalar_one_or_none()
        if recruiter:
            recruiter.current_load = max(0, (recruiter.current_load or 0) - 1)
    except Exception as e:
        logger.error(f"Load release failed for recruiter '{recruiter_username}': {e}")


async def recompute_recruiter_loads(db) -> dict:
    """Set every recruiter's current_load to the true count of ACTIVE APPLICANTS
    (Deck pipeline cards in DECK_ACTIVE_STACKS) assigned to that recruiter.

    Load = applicants only (Deck cards). DB recruit/active members do NOT count —
    they are past the recruiter's pipeline job. Single source of truth; kills the
    drifting += 1 counters. Does NOT touch total_recruited. Commits + returns
    {nc_username: new_load}.
    """
    from sqlalchemy import select as _select
    recruiters = (await db.execute(_select(Recruiter))).scalars().all()

    # Count Deck card assignments per recruiter nc_username across active stacks.
    load_by_user: dict[str, int] = {}
    try:
        url = f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()
            for stack in resp.json():
                if stack.get("id") not in DECK_ACTIVE_STACKS:
                    continue
                for card in stack.get("cards", []):
                    if card.get("archived"):
                        continue
                    for au in card.get("assignedUsers", []):
                        uid = (au.get("participant", {}) or {}).get("uid", "").strip()
                        if uid:
                            load_by_user[uid] = load_by_user.get(uid, 0) + 1
    except Exception as e:
        logger.error(f"recompute_recruiter_loads: Deck fetch failed: {e}")

    result = {}
    for r in recruiters:
        r.current_load = load_by_user.get(r.nc_username, 0)
        result[r.nc_username] = r.current_load
    await db.commit()
    logger.info(f"recompute_recruiter_loads (applicants): {result}")
    return result

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


from app.services import ranks as _ranks_s1


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
        r.rank_display = _ranks_s1.abbr_map().get(m.rank_grade, "") if m else ""
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
            m.rank_display = _ranks_s1.abbr_map().get(m.rank_grade, "")
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

async def _fetch_full_pipeline(db=None) -> dict:
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

                # Auto-ARCHIVE completed cards older than 10 days (was: delete).
                # Archiving preserves the card (and its recruiter assignment /
                # history) in Deck instead of destroying it, so recruiter-credit
                # and onboarding analytics retain a trail. Deck archives via
                # PUT /cards/{id} with archived=true (title is required by the API).
                if stack_id == 16:
                    stale = [c for c in cards if c["days"] >= 10]
                    for stale_card in stale:
                        try:
                            await client.put(
                                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/16/cards/{stale_card['id']}",
                                headers={"OCS-APIRequest": "true"},
                                auth=(NC_SVC_USER, NC_SVC_PASS),
                                json={"title": stale_card.get("name") or "Recruit", "archived": True},
                                timeout=10,
                            )
                            logger.info(f"Auto-archived completed pipeline card: {stale_card['name']} ({stale_card['days']}d old)")
                        except Exception as e:
                            logger.error(f"Failed to auto-archive pipeline card {stale_card['id']}: {e}")
                    cards = [c for c in cards if c["days"] < 10]

                columns[stack_id] = {
                    "name": DECK_STACKS[stack_id],
                    "cards": cards,
                    "count": len(cards),
                }
    except Exception:
        pass
    # Enrich cards with the recruiter of record (member.assigned_recruiter),
    # resolved to a display name. Falls back to Deck card assignedUsers.
    if db is not None:
        try:
            from app.models.member import Member as _M
            from app.models.recruiting import Recruiter as _R
            from sqlalchemy import select as _sel
            mres = await db.execute(_sel(_M.first_name, _M.last_name, _M.assigned_recruiter))
            by_name = {}
            for fn, ln, rec in mres.all():
                if rec:
                    by_name[((fn or "").strip().lower(), (ln or "").strip().lower())] = rec.strip()
            rres = await db.execute(_sel(_R.nc_username, _R.display_name))
            disp = {(u or "").strip(): (d or u) for u, d in rres.all()}
            for col in columns.values():
                for c in col.get("cards", []):
                    parts = (c.get("name") or "").split()
                    rec = None
                    if len(parts) >= 2:
                        rec = by_name.get((parts[0].strip().lower(), parts[-1].strip().lower()))
                    if rec:
                        c["assigned"] = [disp.get(rec, rec)]
        except Exception as _e:
            import logging as _lg
            _lg.getLogger(__name__).warning(f"pipeline recruiter enrich failed: {_e}")

    return columns


@router.get("/pipeline")
@require_auth
async def pipeline_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Kanban-style pipeline dashboard."""
    user = request.session.get("user", {})
    require_pipeline(user)

    columns = await _fetch_full_pipeline(db)

    result = await db.execute(select(Recruiter).where(Recruiter.is_active == True).order_by(Recruiter.display_name))
    recruiters = result.scalars().all()

    return templates.TemplateResponse("pages/s1_pipeline.html", {
        "request": request,
        "user": user,
        "columns": columns,
        "stack_order": [11, 12, 13, 14, 15, 16, 81],
        "recruiters": recruiters,
    })


@router.get("/pipeline/board")
@require_auth
async def pipeline_board_partial(request: Request, db: AsyncSession = Depends(get_db)):
    """Return just the kanban board columns (HTMX partial refresh)."""
    user = request.session.get("user", {})
    require_pipeline(user)

    columns = await _fetch_full_pipeline(db)

    result = await db.execute(select(Recruiter).where(Recruiter.is_active == True).order_by(Recruiter.display_name))
    recruiters = result.scalars().all()

    return templates.TemplateResponse("partials/pipeline_board.html", {
        "request": request,
        "user": user,
        "columns": columns,
        "stack_order": [11, 12, 13, 14, 15, 16, 81],
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

    rank = _ranks_s1.abbr_map().get(member.rank_grade, "")
    display = f"{rank} {member.last_name}".strip()

    recruiter = Recruiter(
        nc_username=member.nc_username,
        display_name=display,
        max_load=max_load,
    )
    db.add(recruiter)
    await db.commit()

    return HTMLResponse(f'<p style="color: #2e7d32; font-weight: 600;">✅ {display} added as recruiter</p>')


@router.post("/recruiters/remove/{recruiter_id}")
@require_auth
async def remove_recruiter(request: Request, recruiter_id: int, db: AsyncSession = Depends(get_db)):
    """Remove a recruiter from the roster. Does NOT unassign their existing
    Deck cards — those keep the Nextcloud user assignment; this just drops the
    recruiter record so they no longer appear in the assign dropdown / roster."""
    user = request.session.get("user", {})
    require_s1(user)

    result = await db.execute(select(Recruiter).where(Recruiter.id == recruiter_id))
    recruiter = result.scalar_one_or_none()
    if not recruiter:
        raise HTTPException(status_code=404, detail="Recruiter not found")

    await db.delete(recruiter)
    await db.commit()

    # Return empty so HTMX removes the row.
    return HTMLResponse("")


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

                # Persist recruiter credit when reaching the Complete stack
                # (15 -> 16). This makes credit durable in the DB regardless of
                # the Deck card later auto-purging from the Complete stack.
                credit_status = ""
                if next_stack_id == 16:
                    credit_status = await _credit_recruiter_on_completion(card_title)

                return HTMLResponse(
                    f'<div style="display:flex;align-items:center;gap:8px;">'
                    f'<span style="color:#2e7d32;font-weight:600;">✅ → {next_name}</span>'
                    f'{welcome_status}'
                    f'{credit_status}'
                    f'<span style="color:#888;font-size:11px;">by {by}</span>'
                    f'</div>',
                    headers={"HX-Trigger": "pipelineChanged"},
                )
            else:
                return HTMLResponse(f'<span style="color:#c62828;">❌ Move failed ({move_resp.status_code})</span>')

    except Exception as e:
        return HTMLResponse(f'<span style="color:#c62828;">❌ Error: {e}</span>')


ON_HOLD_STACK = 81


@router.post("/pipeline/{card_id}/hold")
@require_auth
async def hold_applicant(request: Request, card_id: int):
    """Move a card to On Hold. Stores the original stack in a comment for resume."""
    user = request.session.get("user", {})
    require_pipeline(user)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            current_stack_id = None
            card_title = "Unknown"
            card_desc = ""
            resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()
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

            if current_stack_id == ON_HOLD_STACK:
                return HTMLResponse('<span style="color:#888;">Already on hold</span>')

            # Move to On Hold
            move_resp = await client.put(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/{ON_HOLD_STACK}/cards/{card_id}",
                headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                json={"title": card_title, "description": card_desc, "type": "plain", "order": 999, "owner": NC_SVC_USER},
            )

            if move_resp.status_code in (200, 201):
                by = user.get("display_name", user.get("uid", "unknown"))
                from_stage = DECK_STACKS.get(current_stack_id, "unknown")
                await client.post(
                    f"{NC_URL}/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments",
                    headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                    auth=(NC_SVC_USER, NC_SVC_PASS),
                    json={"message": f"⏸️ Placed **On Hold** by {by} via Portal (was in {from_stage}) [return_stack:{current_stack_id}]"},
                )
                return HTMLResponse(
                    f'<span style="color:#f39c12;font-weight:600;">⏸️ On Hold</span>',
                    headers={"HX-Trigger": "pipelineChanged"},
                )
            else:
                return HTMLResponse(f'<span style="color:#c62828;">❌ Move failed ({move_resp.status_code})</span>')

    except Exception as e:
        return HTMLResponse(f'<span style="color:#c62828;">❌ Error: {e}</span>')


@router.post("/pipeline/{card_id}/resume")
@require_auth
async def resume_applicant(request: Request, card_id: int):
    """Move a card from On Hold back to its previous stage."""
    user = request.session.get("user", {})
    require_pipeline(user)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Verify card is in On Hold
            current_stack_id = None
            card_title = "Unknown"
            card_desc = ""
            resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            resp.raise_for_status()
            for stack in resp.json():
                for card in stack.get("cards", []):
                    if card["id"] == card_id:
                        current_stack_id = stack["id"]
                        card_title = card.get("title", "Unknown")
                        card_desc = card.get("description", "")
                        break
                if current_stack_id:
                    break

            if current_stack_id != ON_HOLD_STACK:
                return HTMLResponse('<span style="color:#c62828;">❌ Card is not on hold</span>')

            # Find the return stack from comments
            import re as _re
            comments_resp = await client.get(
                f"{NC_URL}/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            return_stack = 11  # Default to New Application if we can't find it
            if comments_resp.status_code == 200:
                for comment in comments_resp.json().get("ocs", {}).get("data", []):
                    msg = comment.get("message", "")
                    m = _re.search(r'\[return_stack:(\d+)\]', msg)
                    if m:
                        return_stack = int(m.group(1))
                        break

            # Move back
            move_resp = await client.put(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks/{return_stack}/cards/{card_id}",
                headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
                json={"title": card_title, "description": card_desc, "type": "plain", "order": 999, "owner": NC_SVC_USER},
            )

            if move_resp.status_code in (200, 201):
                by = user.get("display_name", user.get("uid", "unknown"))
                to_stage = DECK_STACKS.get(return_stack, "previous stage")
                await client.post(
                    f"{NC_URL}/ocs/v2.php/apps/deck/api/v1.0/cards/{card_id}/comments",
                    headers={"OCS-APIRequest": "true", "Content-Type": "application/json", "Accept": "application/json"},
                    auth=(NC_SVC_USER, NC_SVC_PASS),
                    json={"message": f"▶️ Resumed from On Hold by {by} via Portal → {to_stage}"},
                )
                return HTMLResponse(
                    f'<span style="color:#2e7d32;font-weight:600;">▶️ → {to_stage}</span>',
                    headers={"HX-Trigger": "pipelineChanged"},
                )
            else:
                return HTMLResponse(f'<span style="color:#c62828;">❌ Resume failed ({move_resp.status_code})</span>')

    except Exception as e:
        return HTMLResponse(f'<span style="color:#c62828;">❌ Error: {e}</span>')


@router.post("/pipeline/{card_id}/decline")
@require_auth
async def decline_applicant(request: Request, card_id: int, db: AsyncSession = Depends(get_db)):
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

            # Release the assigned recruiter's pipeline load (declined recruit
            # leaves the active pipeline without completing).
            if card_data:
                member = await _match_member_by_card_title(db, card_data.get("title", ""))
                if member:
                    await _release_recruiter_load(db, member)
                    await db.commit()

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
            deck_html = '<p style="color:#888;font-size:12px;">No Deck attachments.</p>'
        else:
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

                preview_url = f"/api/s1/pipeline/{card_id}/attachments/{att_id}/preview"
                dl_url = f"/api/s1/pipeline/{card_id}/attachments/{att_id}/download"

                # Images get inline preview; PDFs open in-browser; others download
                from html import escape as h
                safe_name = h(name, quote=True)
                if ext in ("jpg", "jpeg", "png", "gif", "webp", "pdf"):
                    html_parts.append(f'''
                    <div style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:11px;">
                        <span>{icon}</span>
                        <a href="#" onclick="openFilePreview('{preview_url}','{dl_url}','{safe_name}');return false;" style="color:#d4a537;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;cursor:pointer;" title="{safe_name}">{safe_name}</a>
                        <a href="{dl_url}" style="color:#888;font-size:10px;text-decoration:none;flex-shrink:0;" title="Download">⬇</a>
                    </div>''')
                else:
                    html_parts.append(f'''
                    <div style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:11px;">
                        <span>{icon}</span>
                        <a href="{dl_url}" target="_blank" style="color:#d4a537;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;" title="{safe_name}">{safe_name}</a>
                    </div>''')

            deck_html = "".join(html_parts)

        # Also fetch portal-uploaded files (background checks, etc.)
        portal_html = await _get_portal_uploads_html(card_id)

        # Upload form
        upload_html = f'''
        <div style="margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.1);overflow:hidden;">
            <form hx-post="/api/s1/pipeline/{card_id}/upload" hx-target="#attach-{card_id}" hx-swap="innerHTML" hx-encoding="multipart/form-data">
                <div style="margin-bottom:4px;">
                    <input type="file" name="file" required style="width:100%;font-size:10px;color:#ccc;box-sizing:border-box;">
                </div>
                <div style="display:flex;gap:4px;">
                    <select name="doc_type" style="flex:1;padding:2px 4px;font-size:10px;background:#2a2a3e;color:#eee;border:1px solid #444;border-radius:3px;min-width:0;">
                        <option value="Background Check">BG Check</option>
                        <option value="Other">Other</option>
                    </select>
                    <button hx-disabled-elt="this" type="submit" style="padding:3px 8px;background:#d4a537;color:#1a1a2e;border:none;border-radius:3px;cursor:pointer;font-size:10px;font-weight:600;flex-shrink:0;">📤 Upload</button>
                </div>
            </form>
        </div>'''

        return HTMLResponse(deck_html + portal_html + upload_html)

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


@router.get("/pipeline/{card_id}/attachments/{attachment_id}/preview")
@require_auth
async def preview_attachment(request: Request, card_id: int, attachment_id: int):
    """Proxy attachment for inline preview (Content-Disposition: inline)."""
    from fastapi.responses import StreamingResponse

    user = request.session.get("user", {})
    require_pipeline(user)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
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
            # Force inline display
            return StreamingResponse(
                iter([resp.content]),
                media_type=content_type,
                headers={"Content-Disposition": "inline"},
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Portal-side file uploads (background checks, etc.) ─────────────────

async def _get_card_title(card_id: int) -> str:
    """Get the card title from Deck to derive the member folder name."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            stacks_resp = await client.get(
                f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{DECK_BOARD_ID}/stacks",
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=(NC_SVC_USER, NC_SVC_PASS),
            )
            stacks_resp.raise_for_status()
            for stack in stacks_resp.json():
                for card in stack.get("cards", []):
                    if card["id"] == card_id:
                        return card.get("title", "")
    except Exception:
        pass
    return ""


async def _get_portal_uploads_html(card_id: int) -> str:
    """List portal-uploaded files for a card from NC WebDAV."""
    card_title = await _get_card_title(card_id)
    if not card_title:
        return ""

    nc_base = "/remote.php/dav/files/spooky/13th%20Legion%20Shared/%5bS-1%5d%20Admin/Personnel"
    folder_path = f"{NC_URL}{nc_base}/{quote(card_title)}/Pipeline"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(
                "PROPFIND",
                folder_path,
                headers={"Depth": "1"},
                auth=(NC_SPOOKY_USER, NC_SPOOKY_PASS),
            )
            if resp.status_code == 404:
                return ""  # No uploads yet
            resp.raise_for_status()

            import re
            hrefs = re.findall(r'<d:href>([^<]+)</d:href>', resp.text)
            files = []
            for href in hrefs[1:]:  # Skip the folder itself
                filename = unquote(href.rsplit("/", 1)[-1])
                if not filename:
                    continue
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                icon = "📕" if ext == "pdf" else "🖼️" if ext in ("jpg", "jpeg", "png", "gif", "webp") else "📄"
                view_url = f"/api/s1/pipeline/{card_id}/portal-file/{quote(filename)}"
                dl_url = f"/api/s1/pipeline/{card_id}/portal-file/{quote(filename)}?download=1"

                from html import escape as h
                safe_fn = h(filename, quote=True)
                if ext in ("jpg", "jpeg", "png", "gif", "webp", "pdf"):
                    files.append(f'''
                    <div style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:11px;">
                        <span>{icon}</span>
                        <a href="#" onclick="openFilePreview('{view_url}','{dl_url}','{safe_fn}');return false;" style="color:#81c784;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;cursor:pointer;" title="{safe_fn}">{safe_fn}</a>
                        <a href="{dl_url}" style="color:#888;font-size:10px;text-decoration:none;flex-shrink:0;" title="Download">⬇</a>
                    </div>''')
                else:
                    files.append(f'''
                    <div style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:11px;">
                        <span>{icon}</span>
                        <a href="{dl_url}" style="color:#81c784;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;" title="{safe_fn}">{safe_fn}</a>
                    </div>''')

            if not files:
                return ""
            return '<div style="margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.08);">' + "".join(files) + "</div>"

    except Exception:
        return ""


@router.post("/pipeline/{card_id}/upload")
@require_auth
async def upload_pipeline_file(request: Request, card_id: int):
    """Upload a file (background check, etc.) to the member's NC Personnel folder."""
    from fastapi import UploadFile, File, Form

    user = request.session.get("user", {})
    require_pipeline(user)

    form = await request.form()
    file = form.get("file")
    doc_type = form.get("doc_type", "Other")

    if not file or not hasattr(file, "read"):
        return HTMLResponse('<p style="color:#c62828;font-size:12px;">No file selected.</p>')

    card_title = await _get_card_title(card_id)
    if not card_title:
        return HTMLResponse('<p style="color:#c62828;font-size:12px;">Could not find card.</p>')

    # Read file content
    content = await file.read()
    original_name = file.filename or "upload"
    ext = original_name.rsplit(".", 1)[-1] if "." in original_name else "bin"

    # Name the file: "BG Check - LastName FirstName.pdf" or similar
    from datetime import date
    safe_type = doc_type.replace("/", "-").replace("\\", "-")
    filename = f"{safe_type} - {card_title} - {date.today().isoformat()}.{ext}"

    nc_base = "/remote.php/dav/files/spooky/13th%20Legion%20Shared/%5bS-1%5d%20Admin/Personnel"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Ensure Pipeline subfolder exists
            folder_path = f"{NC_URL}{nc_base}/{quote(card_title)}/Pipeline"

            # Create parent folder if needed
            await client.request("MKCOL", f"{NC_URL}{nc_base}/{quote(card_title)}",
                                 auth=(NC_SPOOKY_USER, NC_SPOOKY_PASS))
            await client.request("MKCOL", folder_path,
                                 auth=(NC_SPOOKY_USER, NC_SPOOKY_PASS))

            # Upload
            upload_path = f"{folder_path}/{quote(filename)}"
            resp = await client.put(
                upload_path,
                content=content,
                auth=(NC_SPOOKY_USER, NC_SPOOKY_PASS),
                headers={"Content-Type": "application/octet-stream"},
            )
            resp.raise_for_status()

    except Exception as e:
        log.error(f"Pipeline upload failed for card {card_id}: {e}")
        return HTMLResponse(f'<p style="color:#c62828;font-size:12px;">Upload failed: {e}</p>')

    # Re-render the full attachments panel
    return await get_card_attachments(request, card_id)


@router.get("/pipeline/{card_id}/portal-file/{filename}")
@require_auth
async def serve_portal_file(request: Request, card_id: int, filename: str):
    """Proxy a portal-uploaded file from NC WebDAV."""
    from fastapi.responses import StreamingResponse

    user = request.session.get("user", {})
    require_pipeline(user)

    card_title = await _get_card_title(card_id)
    if not card_title:
        raise HTTPException(status_code=404, detail="Card not found")

    nc_base = "/remote.php/dav/files/spooky/13th%20Legion%20Shared/%5bS-1%5d%20Admin/Personnel"
    file_path = f"{NC_URL}{nc_base}/{quote(card_title)}/Pipeline/{quote(filename)}"

    download = request.query_params.get("download") == "1"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(file_path, auth=(NC_SPOOKY_USER, NC_SPOOKY_PASS))
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "application/octet-stream")
            disposition = "attachment" if download else "inline"

            return StreamingResponse(
                iter([resp.content]),
                media_type=content_type,
                headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
            )

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

        # Recompute recruiter loads from Deck (single source of truth).
        await recompute_recruiter_loads(db)

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

    # Leave-eligible = patched members (have a patch_date), active/recruit
    leave_eligible = [m for m in active_members if m.patch_date]

    # Members currently on leave (audit visibility)
    on_leave_result = await db.execute(
        select(Member)
        .where(Member.on_leave == True)  # noqa: E712
        .order_by(Member.leave_end)
    )
    on_leave_members = on_leave_result.scalars().all()
    for _m in on_leave_members:
        _m.rank_display = _ranks_s1.abbr_map().get(_m.rank_grade, "")

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
            member.rank_display = _ranks_s1.abbr_map().get(member.rank_grade, "")
        recent_separations.append({"log": log, "member": member})

    # Separations whose mechanical cleanup never completed. Deliberately NOT
    # limited to the recent window: Rankin's sat unresolved for two months and
    # would never have appeared in a "last 20" list. The host reconciler flips
    # these flags once it confirms the NC side is actually clean, so anything
    # still listed here is either genuinely dirty or has never been reconciled.
    #
    # Two filters matter here, both learned by getting it wrong first:
    #  * Only members who are CURRENTLY separated/blacklisted. A reactivated
    #    member is not an unfinished offboarding.
    #  * Exclude reactivation entries. reactivate_member() writes its audit
    #    trail into this same separation_log table with a reason like
    #    "reactivated (inactive -> active)", and those rows naturally have the
    #    cleanup flags false -- flagging them as incomplete cleanup is
    #    nonsense and would have made this panel permanent noise.
    stale_result = await db.execute(
        select(SeparationLog, Member)
        .join(Member, SeparationLog.member_id == Member.id)
        .where(
            Member.status.in_(("separated", "blacklisted")),
            ~SeparationLog.reason.ilike("reactivated%"),
            (SeparationLog.nc_account_disabled.is_(False))
            | (SeparationLog.groups_removed.is_(False))
            | (SeparationLog.talk_removed.is_(False))
            | (SeparationLog.tokens_revoked.is_(False)),
        )
        .order_by(desc(SeparationLog.created_at))
        .limit(50)
    )
    unresolved_cleanup = []
    for row in stale_result.all():
        log, member = row[0], row[1]
        if member:
            member.rank_display = _ranks_s1.abbr_map().get(member.rank_grade, "")
        unresolved_cleanup.append({"log": log, "member": member})

    return templates.TemplateResponse("pages/s1_offboard.html", {
        "request": request,
        "user": user,
        "active_members": active_members,
        "leave_eligible": leave_eligible,
        "on_leave_members": on_leave_members,
        "recent_separations": recent_separations,
        "unresolved_cleanup": unresolved_cleanup,
        "now": datetime.utcnow(),
    })


@router.post("/offboard/{member_id}")
@require_auth
async def process_offboarding(request: Request, member_id: int, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
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

    # If this member is still in the active recruit pipeline, releasing them
    # frees their recruiter's current_load. Capture before the status flips.
    was_recruit = member.status == "recruit"

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

    # ── Mechanical cleanup, with HONEST per-step reporting ──────────────────
    # This block previously rendered only the steps that SUCCEEDED, so a failed
    # group removal looked identical to one that was never requested. That is
    # exactly how RCT Rankin's half-finished separation (2026-06-29) went
    # unnoticed until 2026-09-03: separation_log recorded groups_removed=false
    # and the operator was shown a green success box.
    #
    # steps entries are (state, label, detail) where state is one of:
    #   ok | fail | skip | pending
    steps: list[tuple[str, str, str]] = []

    if disable_nc and member.nc_username:
        try:
            async with httpx.AsyncClient(auth=(NC_SVC_USER, NC_SVC_PASS)) as client:
                resp = await client.put(
                    f"{NC_URL}/ocs/v2.php/cloud/users/{member.nc_username}/disable",
                    headers={"OCS-APIRequest": "true"},
                    timeout=15,
                )
            log_entry.nc_account_disabled = resp.status_code in (200, 100)
            detail = "" if log_entry.nc_account_disabled else f"OCS returned HTTP {resp.status_code}"
        except Exception as e:
            log_entry.nc_account_disabled = False
            detail = str(e)
            logger.error(f"NC disable failed for {member.nc_username}: {e}")
        steps.append(("ok" if log_entry.nc_account_disabled else "fail",
                      "NC account disabled", detail))
    else:
        steps.append(("skip", "NC account disabled",
                      "not requested" if member.nc_username else "no NC username on file"))

    if remove_groups and member.nc_username:
        ok, failed_groups = await _nc_remove_all_groups(member.nc_username)
        log_entry.groups_removed = ok
        steps.append(("ok" if ok else "fail", "NC groups removed",
                      "" if ok else "still a member of: " + ", ".join(failed_groups)))
    else:
        steps.append(("skip", "NC groups removed",
                      "not requested" if member.nc_username else "no NC username on file"))

    # Talk eviction and device-token revocation CANNOT be performed from this
    # container: praetorium-app has no docker socket and no docker binary, so
    # it cannot run occ, and Talk's API won't let an admin enumerate another
    # user's rooms. Both steps genuinely matter -- a disabled NC account is
    # STILL a Talk participant and its device tokens STILL authenticate, which
    # is what made Rankin's Android client throw a 503 every ~20 minutes for
    # two months. The host-side `offboard-reconcile` timer does them and flips
    # these flags. Report as pending; never claim them as done here.
    if member.nc_username:
        steps.append(("pending", "Talk rooms evicted", "queued for host reconciler"))
        steps.append(("pending", "Device tokens revoked", "queued for host reconciler"))

    log_entry.portal_access_revoked = revoke_portal
    steps.append(("ok" if revoke_portal else "skip", "Portal access revoked",
                  "" if revoke_portal else "not requested"))
    db.add(log_entry)

    # Release recruiter load if this was an active recruit (not a patched member).
    if was_recruit:
        await _release_recruiter_load(db, member)

    await db.commit()

    # Send separation notification email in background (smtplib is synchronous)
    if member.email:
        background_tasks.add_task(_send_offboard_email, member, reason, notes)

    # Email is queued after commit, so report it as a step too.
    steps.append(("ok" if member.email else "fail", "Separation email queued",
                  "" if member.email else "no email on file — member was NOT notified"))

    # Render every step, failures included. A single failed step turns the whole
    # banner red: the operator must never be able to read this as "done" when
    # part of it did not happen.
    _ICON = {"ok": "✅", "fail": "❌", "skip": "⏭️", "pending": "⏳"}
    _COLOR = {"ok": "#2e7d32", "fail": "#b71c1c", "skip": "#777", "pending": "#e65100"}
    failed = [s for s in steps if s[0] == "fail"]
    pending = [s for s in steps if s[0] == "pending"]

    rows = "".join(
        f'<div style="margin-top:4px;color:{_COLOR[state]}">'
        f'{_ICON[state]} {escape(label)}'
        + (f' <span style="color:#888;font-size:12px">— {escape(detail)}</span>' if detail else "")
        + "</div>"
        for state, label, detail in steps
    )

    if failed:
        banner_bg, banner_edge = "#ffebee", "#b71c1c"
        headline = (
            f"<strong>Separated with {len(failed)} FAILED step"
            f"{'s' if len(failed) > 1 else ''}:</strong> "
            f"{escape(member.first_name)} {escape(member.last_name)} — {escape(reason)}"
            "<div style='margin-top:6px;font-size:13px'>The separation is recorded, but the "
            "cleanup below is incomplete. Re-run it or fix it by hand — a disabled account that "
            "keeps its groups, Talk rooms or device tokens will keep erroring against the "
            "server indefinitely.</div>"
        )
    else:
        banner_bg, banner_edge = "#fff3e0", "#e65100"
        headline = (
            f"<strong>Separated:</strong> {escape(member.first_name)} "
            f"{escape(member.last_name)} — {escape(reason)}"
        )

    pending_note = ""
    if pending:
        pending_note = (
            "<div style='margin-top:10px;padding:8px 10px;background:#fff8e1;"
            "border-left:3px solid #e65100;font-size:12px;color:#555'>"
            "⏳ Talk eviction and token revocation run on the host "
            "(<code>offboard-reconcile</code>, every 10 min) because this container has no "
            "occ access. They'll show as complete in <em>Recent Separations</em> once done. "
            "If they're still outstanding after ~15 minutes, that timer is broken — check it."
            "</div>"
        )

    return HTMLResponse(f"""
        <div style="padding: 16px; background: {banner_bg}; border-left: 4px solid {banner_edge}; border-radius: 4px;">
            {headline}
            <div style="margin-top:10px">{rows}</div>
            {pending_note}
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
# CODE_OF_CONDUCT_TEXT and BYLAWS_TEXT imported from app.routes.doc_texts


# ─── PP-107: S1 Email Blast ─────────────────────────────────────────────────

# Recipient groups + resolver are defined once in newsletter_send so Unit Comms
# and the Legionary Dispatch newsletter always target identical audiences.
from app.newsletter_send import resolve_recipient_emails  # noqa: E402
from app.routes.events import _build_recipient_groups  # noqa: E402


@router.get("/email-blast")
@require_auth
async def email_blast_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Email blast compose page."""
    user = request.session.get("user", {})
    require_unit_comms(user)

    # Use the SAME rich, team-aware recipient registry that events use so Unit
    # Comms gets the full group granularity (teams, individual shops, NCOs, etc).
    groups = await _build_recipient_groups(db)
    return templates.TemplateResponse("pages/s1_email_blast.html", {
        "request": request,
        "user": user,
        "groups": groups,
    })


@router.post("/email-blast/preview", response_class=HTMLResponse)
@require_auth
async def email_blast_preview(request: Request, db: AsyncSession = Depends(get_db)):
    """Preview recipients for selected groups."""
    user = request.session.get("user", {})
    require_unit_comms(user)

    form = await request.form()
    selected_groups = form.getlist("groups")

    if not selected_groups:
        return HTMLResponse('<div style="color:#ef5350;padding:8px;">Select at least one group.</div>')

    # Shared resolver — same rich group registry as events.
    recipients = await resolve_recipient_emails(db, selected_groups)
    recipients.sort(key=lambda r: r[1].split()[-1] if r[1] else "")

    if not recipients:
        return HTMLResponse('<div style="color:#ef5350;padding:8px;">No recipients have email addresses on file.</div>')

    names_html = ", ".join(
        f'<span style="color:#ccc;">{name}</span>' for _email, name in recipients
    )

    return HTMLResponse(f'''
        <div style="padding:12px;background:rgba(212,165,55,0.1);border:1px solid rgba(212,165,55,0.3);border-radius:6px;margin-top:8px;">
            <div style="font-weight:600;color:#d4a537;margin-bottom:6px;">📨 {len(recipients)} recipients:</div>
            <div style="font-size:12px;line-height:1.6;">{names_html}</div>
        </div>
    ''')


@router.post("/email-blast/send", response_class=HTMLResponse, response_model=None)
@require_auth
async def send_email_blast(request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Send an email blast to selected groups (with optional inline images +
    file attachments)."""
    user = request.session.get("user", {})
    require_unit_comms(user)

    form = await request.form()
    selected_groups = form.getlist("groups")
    subject = form.get("subject", "").strip()
    body = form.get("body", "").strip()

    if not subject:
        return HTMLResponse('<div style="color:#ef5350;padding:8px;">Subject is required.</div>')
    if not body:
        return HTMLResponse('<div style="color:#ef5350;padding:8px;">Message body is required.</div>')
    if not selected_groups:
        return HTMLResponse('<div style="color:#ef5350;padding:8px;">Select at least one group.</div>')

    # Sanitize body (now that it can carry inline <img> embeds).
    import bleach as _bleach
    body = _bleach.clean(
        body,
        tags=["p", "br", "strong", "em", "u", "s", "h1", "h2", "h3", "ul", "ol", "li",
              "blockquote", "a", "img", "span", "div"],
        attributes={"a": ["href", "title", "target", "rel"],
                    "img": ["src", "alt", "width", "height", "style"],
                    "span": ["style"], "div": ["style"]},
        protocols=["http", "https", "mailto"], strip=True,
    )

    # ── Optional attachments (multipart "attachments" files) ──────────────────
    import uuid as _uuid
    from app.newsletter_assets import (
        NEWSLETTER_ATTACH_DIR, ALLOWED_ATTACH_MIMES,
        MAX_ATTACH_BYTES, MAX_TOTAL_ATTACH_BYTES,
    )
    staged_atts: list[tuple[str, str, str]] = []  # (disk_path, orig_name, mime)
    total_bytes = 0
    for up in form.getlist("attachments"):
        if not getattr(up, "filename", None):
            continue
        fdata = await up.read()
        fmime = up.content_type or "application/octet-stream"
        if fmime not in ALLOWED_ATTACH_MIMES:
            return HTMLResponse(f'<div style="color:#ef5350;padding:8px;">Unsupported attachment type: {up.filename}</div>')
        if len(fdata) > MAX_ATTACH_BYTES:
            return HTMLResponse(f'<div style="color:#ef5350;padding:8px;">{up.filename} exceeds {MAX_ATTACH_BYTES // (1024*1024)}MB.</div>')
        total_bytes += len(fdata)
        if total_bytes > MAX_TOTAL_ATTACH_BYTES:
            return HTMLResponse(f'<div style="color:#ef5350;padding:8px;">Attachments exceed {MAX_TOTAL_ATTACH_BYTES // (1024*1024)}MB total (Proton limit).</div>')
        NEWSLETTER_ATTACH_DIR.mkdir(parents=True, exist_ok=True)
        _ext = os.path.splitext(up.filename)[1].lower()
        _stored = f"blast_{_uuid.uuid4().hex}{_ext}"
        with open(NEWSLETTER_ATTACH_DIR / _stored, "wb") as _fh:
            _fh.write(fdata)
        staged_atts.append((str(NEWSLETTER_ATTACH_DIR / _stored), up.filename, fmime))

    # Shared resolver — same rich group registry as events.
    recipient_emails = await resolve_recipient_emails(db, selected_groups)

    if not recipient_emails:
        return HTMLResponse('<div style="color:#ef5350;padding:8px;">No recipients with email addresses.</div>')

    recipient_count = len(recipient_emails)

    sender_name = user.get("display_name", user.get("username", "S1"))

    # Send in background to avoid timeout
    def _send_blast():
        import re as _re
        from app.newsletter_send import send_email_sync

        html_body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #1a1a2e; line-height: 1.6; max-width: 650px; margin: 0 auto;">
<div style="background: #1a1a2e; padding: 20px; text-align: center;">
    <table style="margin: 0 auto;" cellpadding="0" cellspacing="0"><tr>
        <td style="vertical-align: middle; padding-right: 15px;">
            <img src="https://13thlegion.org/assets/img/crest.png" alt="13th Legion" height="70" style="display: block;">
        </td>
        <td style="vertical-align: middle; text-align: center;">
            <h1 style="color: #d4a537; margin: 0; font-size: 28px;">13TH LEGION</h1>
            <p style="color: #ccc; margin: 5px 0 0;">Texas State Militia — Dallas / Fort Worth</p>
        </td>
        <td style="vertical-align: middle; padding-left: 15px;">
            <img src="https://13thlegion.org/assets/img/tsm-seal.png" alt="TSM" height="70" style="display: block;">
        </td>
    </tr></table>
</div>
<div style="padding: 20px;">
<div style="margin:0;padding:0;">
<style>p{{margin:0 0 0.5em 0;}} ul,ol{{margin:0 0 0.5em 0;}}</style>
{body}
</div>
<p style="margin-top: 20px;">
    <em>Nunquam Non Paratus,</em><br>
    <strong>{sender_name}</strong><br>
    13th Legion, Texas State Militia
</p>
</div>
<div style="background: #1a1a2e; padding: 15px; text-align: center;">
    <p style="color: #d4a537; margin: 0; font-style: italic;">Nunquam Non Paratus — Never Not Ready</p>
    <p style="color: #888; margin: 5px 0 0; font-size: 12px;">
        13th Legion · Texas State Militia · <a href="https://13thlegion.org" style="color: #888;">13thlegion.org</a>
    </p>
</div>
</body></html>"""

        _plain = _re.sub(r'<br\s*/?>', '\n', body)
        _plain = _re.sub(r'</p>\s*<p[^>]*>', '\n\n', _plain)
        _plain = _re.sub(r'<[^>]+>', '', _plain)

        sent, failed, err = send_email_sync(
            subject=subject,
            html_body=html_body,
            plain_body=_plain,
            sender_from=SMTP_FROM,
            recipients=recipient_emails,
            attachment_files=staged_atts,
        )
        logger.info(f"Email blast complete: {sent} sent, {failed} failed — subject: {subject}")

        # Clean up staged attachment files.
        for _path, _n, _m in staged_atts:
            try:
                os.remove(_path)
            except Exception:
                pass

    background_tasks.add_task(_send_blast)

    _all_groups = await _build_recipient_groups(db)
    group_labels = ", ".join(_all_groups[g]["label"] for g in selected_groups if g in _all_groups)
    att_line = f"<br><strong>Attachments:</strong> {len(staged_atts)}" if staged_atts else ""

    return HTMLResponse(f'''
        <div style="padding:16px;background:rgba(39,174,96,0.15);border:1px solid rgba(39,174,96,0.3);border-radius:6px;">
            <div style="font-weight:600;color:#27ae60;margin-bottom:4px;">✅ Email blast queued</div>
            <div style="font-size:13px;color:#ccc;">
                <strong>To:</strong> {group_labels} ({recipient_count} recipients)<br>
                <strong>Subject:</strong> {subject}<br>
                <strong>Sent by:</strong> {sender_name}{att_line}
            </div>
        </div>
    ''')
# ─── PP: 6-Month Leave of Absence (patched members only) ─────────────────────

NC_ON_LEAVE_GROUP = "on-leave"


def _nc_talk_remove(nc_username: str) -> bool:
    """DEAD PATH -- kept only to document why it cannot work from here.

    This used to shell out to:
        docker exec -u www-data nextcloud-app php occ talk:user:remove --user X

    That can NEVER succeed from inside praetorium-app: the container has no
    docker socket mounted and no docker binary on PATH, so subprocess.run
    raised FileNotFoundError, the surrounding `except Exception` swallowed it,
    and offboarding reported success anyway. That is precisely why RCT Rankin
    remained in 5 Talk rooms for two months after being separated -- it was a
    broken mechanism, not a missed checkbox.

    Talk eviction (and device-token revocation) now belong to the host-side
    `offboard-reconcile` systemd timer, which does have docker/occ access. The
    portal records the steps as outstanding on the SeparationLog and the
    reconciler flips them once confirmed.

    Deliberately NOT reintroduced over the Talk OCS API: evicting a user needs
    the list of rooms they belong to, and Talk's API only exposes rooms for the
    *authenticated* user -- an admin cannot enumerate someone else's rooms.
    """
    raise NotImplementedError(
        "Talk eviction is handled by the host-side offboard-reconcile timer; "
        "praetorium-app has no docker/occ access."
    )


async def _nc_list_groups(client: httpx.AsyncClient, nc_username: str) -> list[str]:
    """Groups an NC user belongs to. Raises on transport/HTTP failure."""
    resp = await client.get(
        f"{NC_URL}/ocs/v2.php/cloud/users/{nc_username}/groups",
        headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        params={"format": "json"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return resp.json().get("ocs", {}).get("data", {}).get("groups", []) or []


async def _nc_remove_all_groups(nc_username: str) -> tuple[bool, list[str]]:
    """Remove an NC user from every group they belong to.

    Returns (all_removed, still_member_of). Reports WHICH groups are still
    attached rather than collapsing everything to one bool.

    Correctness note: success is decided by RE-LISTING the user's groups
    afterwards, not by parsing the DELETE response. The OCS group-removal
    endpoint does not reliably return a JSON body -- an earlier version of this
    helper tried to read ocs.meta.statuscode from it, hit
    "Expecting value: line 1 column 1", and reported failure for removals that
    had actually succeeded. Ground truth is the group list itself.
    """
    try:
        async with httpx.AsyncClient(auth=(NC_SVC_USER, NC_SVC_PASS)) as client:
            try:
                groups = await _nc_list_groups(client, nc_username)
            except Exception as e:
                return False, [f"<could not list groups: {e}>"]

            for group in groups:
                try:
                    # NB: params=, NOT data=. httpx's .delete() has no `data`
                    # kwarg, so the old data={...} call raised TypeError on
                    # every single offboarding and the bare `except Exception`
                    # hid it -- group removal never once worked.
                    await client.delete(
                        f"{NC_URL}/ocs/v2.php/cloud/users/{nc_username}/groups",
                        headers={"OCS-APIRequest": "true"},
                        params={"groupid": group, "format": "json"},
                        timeout=10,
                    )
                except Exception as e:
                    logger.error(f"group removal call failed for {nc_username}/{group}: {e}")

            # Verify against the server rather than trusting the responses.
            try:
                remaining = await _nc_list_groups(client, nc_username)
            except Exception as e:
                return False, [f"<could not verify groups: {e}>"]
            if remaining:
                logger.error(f"{nc_username} still in groups after removal: {remaining}")
            return (not remaining), remaining
    except Exception as e:
        logger.error(f"group removal aborted for {nc_username}: {e}")
        return False, [f"<error: {e}>"]


async def _nc_group_add(nc_username: str, group: str) -> bool:
    """Add an NC user to a group via OCS API."""
    try:
        async with httpx.AsyncClient(auth=(NC_SVC_USER, NC_SVC_PASS)) as client:
            resp = await client.post(
                f"{NC_URL}/ocs/v2.php/cloud/users/{nc_username}/groups",
                headers={"OCS-APIRequest": "true"},
                data={"groupid": group},
                timeout=15,
            )
            return resp.status_code in (200, 100)
    except Exception as e:
        logger.error(f"NC group add ({group}) failed for {nc_username}: {e}")
        return False


async def _nc_group_remove(nc_username: str, group: str) -> bool:
    """Remove an NC user from a group via OCS API."""
    try:
        async with httpx.AsyncClient(auth=(NC_SVC_USER, NC_SVC_PASS)) as client:
            resp = await client.delete(
                f"{NC_URL}/ocs/v2.php/cloud/users/{nc_username}/groups",
                headers={"OCS-APIRequest": "true"},
                data={"groupid": group},
                timeout=15,
            )
            return resp.status_code in (200, 100)
    except Exception as e:
        logger.error(f"NC group remove ({group}) failed for {nc_username}: {e}")
        return False


def _send_leave_email(member, leave_start, leave_end, kind: str):
    """Send leave-of-absence email. kind = 'start' | 'return'."""
    if not member.email:
        logger.warning(f"No email for {member.first_name} {member.last_name}, skipping leave email")
        return False

    start_str = leave_start.strftime('%B %d, %Y')
    end_str = leave_end.strftime('%B %d, %Y')

    if kind == "start":
        subject = "13th Legion — Leave of Absence Confirmed"
        intro = (
            f"This confirms your approved <strong>6-month leave of absence</strong> from the 13th Legion. "
            f"Welcome to the break — you've earned it."
        )
        body_html = f"""
            <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
                <tr><td style="padding: 6px 0; font-weight: bold; width: 140px;">Leave Start:</td>
                    <td style="padding: 6px 0;">{start_str}</td></tr>
                <tr><td style="padding: 6px 0; font-weight: bold;">Leave End:</td>
                    <td style="padding: 6px 0;">{end_str}</td></tr>
            </table>
            <h3 style="color: #1a1a2e; border-bottom: 1px solid #ddd; padding-bottom: 8px;">During Your Leave</h3>
            <ul style="color: #555;">
                <li>You are <strong>not</strong> required to attend FTXs or any unit events.</li>
                <li>You are <strong>not</strong> required to respond to communications or check in.</li>
                <li>You do <strong>not</strong> need to log into the portal or Nextcloud.</li>
                <li>You are exempt from the unit Activity Policy for the full duration.</li>
                <li>Your account stays active — nothing will be disabled while you're on leave.</li>
            </ul>
            <p>Your leave will automatically end on <strong>{end_str}</strong>, at which point you'll
            return to active status and the Activity Policy will apply again. We'll send you a
            reminder when that day comes.</p>
            <p>Enjoy the time off. The Legion will be here when you get back.</p>
        """
        body_plain = f"""This confirms your approved 6-month leave of absence from the 13th Legion.

Leave Start: {start_str}
Leave End:   {end_str}

During your leave:
- You are NOT required to attend FTXs or any unit events.
- You are NOT required to respond to communications or check in.
- You do NOT need to log into the portal or Nextcloud.
- You are exempt from the unit Activity Policy for the full duration.
- Your account stays active — nothing will be disabled while you're on leave.

Your leave will automatically end on {end_str}, at which point you'll return to
active status and the Activity Policy will apply again.

Enjoy the time off. The Legion will be here when you get back.

Questions? Contact admin@13thlegion.org
"""
    else:  # return
        subject = "13th Legion — Leave of Absence Ended"
        intro = (
            f"Your 6-month leave of absence has officially ended as of <strong>{end_str}</strong>. "
            f"Welcome back to the 13th Legion."
        )
        body_html = f"""
            <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
                <tr><td style="padding: 6px 0; font-weight: bold; width: 140px;">Leave Period:</td>
                    <td style="padding: 6px 0;">{start_str} &ndash; {end_str}</td></tr>
                <tr><td style="padding: 6px 0; font-weight: bold;">Status:</td>
                    <td style="padding: 6px 0;">Active</td></tr>
            </table>
            <h3 style="color: #1a1a2e; border-bottom: 1px solid #ddd; padding-bottom: 8px;">What This Means</h3>
            <ul style="color: #555;">
                <li>You are now <strong>subject to the unit Activity Policy</strong> again.</li>
                <li>You're expected to make reasonable efforts to attend FTXs and maintain
                    regular contact with your team leader.</li>
                <li>Reach out to your team leader to get plugged back in for the next FTX.</li>
            </ul>
            <p>Good to have you back. If you need more time, contact Command before your
            obligations resume — don't just go quiet.</p>
        """
        body_plain = f"""Your 6-month leave of absence has officially ended as of {end_str}.
Welcome back to the 13th Legion.

Leave Period: {start_str} - {end_str}
Status: Active

What this means:
- You are now SUBJECT TO the unit Activity Policy again.
- You're expected to make reasonable efforts to attend FTXs and maintain regular
  contact with your team leader.
- Reach out to your team leader to get plugged back in for the next FTX.

Good to have you back. If you need more time, contact Command before your
obligations resume — don't just go quiet.

Questions? Contact admin@13thlegion.org
"""

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1a1a2e; padding: 20px; text-align: center;">
            <h1 style="color: #d4a537; margin: 0; font-size: 24px;">13th Legion</h1>
            <p style="color: #aaa; margin: 4px 0 0; font-size: 12px;">Texas State Militia</p>
        </div>
        <div style="padding: 24px; background: #f9f9f9; color: #333;">
            <p>Dear {member.first_name},</p>
            <p>{intro}</p>
            {body_html}
            <hr style="border: none; border-top: 1px solid #ddd; margin: 24px 0;">
            <p style="color: #888; font-size: 12px;">
                If you have questions, contact us at
                <a href="mailto:admin@13thlegion.org">admin@13thlegion.org</a>
            </p>
        </div>
    </div>
    """
    plain = f"Dear {member.first_name},\n\n{body_plain}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = member.email
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [member.email], msg.as_string())
        logger.info(f"Leave ({kind}) email sent to {member.email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send leave ({kind}) email to {member.email}: {e}")
        return False


@router.post("/leave/{member_id}")
@require_auth
async def place_on_leave(request: Request, member_id: int, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Place a patched member on a 6-month leave of absence."""
    from datetime import date as _date
    from dateutil.relativedelta import relativedelta

    user = request.session.get("user", {})
    require_s1(user)

    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Eligibility: patched members only
    if not member.patch_date:
        return HTMLResponse(
            '<div style="padding:12px;background:#ffebee;border-left:4px solid #c62828;border-radius:4px;color:#c62828;">'
            '❌ Only patched members are eligible for leave of absence.</div>',
            status_code=400,
        )
    if member.on_leave:
        return HTMLResponse(
            '<div style="padding:12px;background:#fff3e0;border-left:4px solid #e65100;border-radius:4px;color:#e65100;">'
            f'⚠️ {member.first_name} {member.last_name} is already on leave (through {member.leave_end.strftime("%b %d, %Y") if member.leave_end else "?"}).</div>',
            status_code=400,
        )

    start = _date.today()
    end = start + relativedelta(months=6)
    member.on_leave = True
    member.leave_start = start
    member.leave_end = end

    # Add to NC on-leave group (immune to user_retention auto-disable)
    nc_added = False
    if member.nc_username:
        nc_added = await _nc_group_add(member.nc_username, NC_ON_LEAVE_GROUP)

    await db.commit()

    # Email (background — smtplib is sync)
    if member.email:
        background_tasks.add_task(_send_leave_email, member, start, end, "start")

    return HTMLResponse(f"""
        <div style="padding: 16px; background: #e8f5e9; border-left: 4px solid #2e7d32; border-radius: 4px; color:#1b5e20;">
            <strong>On Leave:</strong> {member.first_name} {member.last_name}
            <br>Leave period: {start.strftime('%b %d, %Y')} &rarr; {end.strftime('%b %d, %Y')}
            {'<br>Added to NC on-leave group (retention-exempt) ✅' if nc_added else ('<br>⚠️ NC group add failed — check manually' if member.nc_username else '')}
            {'<br>Leave email queued ✅' if member.email else '<br>⚠️ No email on file'}
        </div>
    """)


async def _process_leave_returns(db: AsyncSession) -> list:
    """Return members whose leave has ended; clear flag, remove NC group, email.
    Intended to be called daily by cron. Returns list of (member, emailed)."""
    from datetime import date as _date
    today = _date.today()
    result = await db.execute(
        select(Member).where(Member.on_leave == True, Member.leave_end < today)  # noqa: E712
    )
    returning = result.scalars().all()
    processed = []
    for m in returning:
        m.on_leave = False
        if m.nc_username:
            await _nc_group_remove(m.nc_username, NC_ON_LEAVE_GROUP)
        # Send return email synchronously (cron context, no BackgroundTasks)
        emailed = False
        if m.email:
            emailed = _send_leave_email(m, m.leave_start, m.leave_end, "return")
        processed.append((m, emailed))
    await db.commit()
    return processed


@router.get("/glance", response_class=HTMLResponse)
@require_auth
async def s1_glance(request: Request):
    """Small at-a-glance stat strip for the S1 dashboard."""
    from app.auth import get_current_user
    from app.database import async_session
    from app.models.training import TrainingClaim

    user = get_current_user(request)
    if not set(user.get("roles", [])) & {"s1", "s1_lead", "command", "admin"}:
        return HTMLResponse('<div style="color:#b71c1c;font-size:13px;">Access denied.</div>', status_code=403)

    async with async_session() as db:
        pending_claims = (await db.execute(
            select(func.count()).select_from(TrainingClaim).where(TrainingClaim.status == "pending")
        )).scalar() or 0
        recruits = (await db.execute(
            select(func.count()).select_from(Member).where(Member.status == "recruit")
        )).scalar() or 0
        active = (await db.execute(
            select(func.count()).select_from(Member).where(Member.status == "active")
        )).scalar() or 0

    def card(icon, label, value, href, warn=False):
        color = "#b71c1c" if warn and value else "#d4a537"
        return (f'<a href="{href}" style="flex:1;min-width:150px;background:#16213e;'
                f'border:1px solid #2a2a4a;border-radius:8px;padding:14px;text-decoration:none;'
                f'display:block;text-align:center;">'
                f'<div style="font-size:20px;margin-bottom:4px;">{icon}</div>'
                f'<div style="color:{color};font-size:24px;font-weight:700;">{value}</div>'
                f'<div style="color:#888;font-size:12px;margin-top:2px;">{label}</div></a>')

    html = '<div style="display:flex;flex-wrap:wrap;gap:12px;">'
    html += card("\U0001F4DD", "Pending training claims", pending_claims,
                 "/api/training/claims/review", warn=True)
    html += card("\U0001F4E5", "Recruits in pipeline", recruits, "/api/s1/pipeline")
    html += card("\U0001F465", "Active members", active, "/roster")
    html += "</div>"
    return HTMLResponse(html)
