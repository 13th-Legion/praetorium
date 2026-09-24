"""S4 Logistics dashboard — meals, expenses, purchasing, donations, inventory.

Five feature areas per the approved spec (`projects/s4-dashboard.md`):

1. Meals & Headcount — per-FTX meal plan (Sat B/L/D + Sun B), headcount from RSVPs.
2. Expense Reimbursement — members submit receipts; S4 reviews → approves →
   marks reimbursed (or rejects). Formalized queue.
3. Purchase Requests — Leaders/S4 request funds; Command or S4 head approves.
4. Equipment Donations — members offer gear; S4 accepts → pending drop-off → received.
5. Supply Inventory — master list with QR codes + a possession log (checkout/check-in).

RBAC:
  * View + submit (expenses, donations, purchase requests): any S4 member,
    Command, or admin (`S4_ROLES`).
  * Approve / reimburse / deny / accept / inventory mutations:
    Command + admin + the S4 *shop head* only (`_can_approve`).

Receipts are stored in Nextcloud under the S4 group folder
(`13th Legion Shared/[S-4] Logistics/Receipts/`) via the service account, same
pattern as training-claim archival.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
import qrcode
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.auth import get_current_user, require_auth
from app.database import get_db
from app.models.events import Event, EventRSVP
from app.models.member import Member
from app.models.s4_logistics import (
    S4Checkout,
    S4EquipmentDonation,
    S4Expense,
    S4InventoryItem,
    S4MealPlan,
    S4PurchaseRequest,
)
from app.settings import NC_SVC_PASS, NC_SVC_USER
from config import get_settings

log = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/api/s4", tags=["s4-admin"])

# Anyone who can see the S4 dashboard may submit. Mutations that spend money or
# move property are gated tighter in _can_approve.
S4_ROLES = {"command", "admin", "s4"}

# Nextcloud group-folder base for receipt storage.
NC_S4_BASE = "/remote.php/dav/files/spooky/13th%20Legion%20Shared/%5bS-4%5d%20Logistics"

FTX_CATEGORIES = ("ftx", "mcftx")
MAX_RECEIPT_BYTES = 10 * 1024 * 1024  # 10MB
RECEIPT_MIMES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}


def _can_view(user: dict) -> bool:
    return bool(user and set(user.get("roles", [])) & S4_ROLES)


async def _current_member(request: Request, db: AsyncSession) -> Member | None:
    user = get_current_user(request)
    if not user:
        return None
    uname = user.get("username")
    res = await db.execute(select(Member).where(Member.nc_username == uname))
    return res.scalar_one_or_none()


def _is_s4_head(member: Member | None) -> bool:
    """True if the member's billets carry 'S4: … (Lead)'."""
    if not member or not member.primary_billet:
        return False
    for part in member.primary_billet.split(","):
        part = part.strip()
        if re.match(r"S4\s*:", part) and "(Lead)" in part:
            return True
    return False


def _can_approve(user: dict, member: Member | None) -> bool:
    roles = set(user.get("roles", []))
    if roles & {"command", "admin"}:
        return True
    return _is_s4_head(member)


def _denied() -> Response:
    return HTMLResponse("<h2>Access Denied</h2>", status_code=403)


async def _get_event_or_none(db: AsyncSession, event_id: int) -> Event | None:
    return (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()


def _display_name(m: Member) -> str:
    """'SGT Wall (Blackout)'-style short name."""
    from app.services import ranks as _ranks
    abbr = _ranks.abbr_map().get(m.rank_grade, m.rank_grade or "")
    base = f"{abbr} {m.last_name}".strip()
    if m.callsign:
        base += f" ({m.callsign})"
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@require_auth
async def s4_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_view(user):
        return _denied()
    member = await _current_member(request, db)
    can_approve = _can_approve(user, member)

    # FTX/MCFTX events, most recent first (for meals + expense event picker).
    events = (await db.execute(
        select(Event).where(Event.category.in_(FTX_CATEGORIES)).order_by(desc(Event.date_start))
    )).scalars().all()

    # Meals: existing plans keyed by event id.
    plans = {
        p.event_id: p for p in (await db.execute(select(S4MealPlan))).scalars().all()
    }
    # Headcount per event = attending RSVPs + guest_count.
    headcounts: dict[int, int] = {}
    for ev in events:
        rows = (await db.execute(
            select(EventRSVP).where(
                EventRSVP.event_id == ev.id,
                EventRSVP.status == "attending",
            )
        )).scalars().all()
        headcounts[ev.id] = sum(1 for _ in rows) + sum(r.guest_count or 0 for r in rows)

    # Expenses (all, newest first).
    expenses = (await db.execute(
        select(S4Expense).order_by(desc(S4Expense.created_at))
    )).scalars().all()

    # Purchase requests.
    purchases = (await db.execute(
        select(S4PurchaseRequest).order_by(desc(S4PurchaseRequest.created_at))
    )).scalars().all()

    # Donations.
    donations = (await db.execute(
        select(S4EquipmentDonation).order_by(desc(S4EquipmentDonation.created_at))
    )).scalars().all()

    # Inventory + possession log.
    inventory = (await db.execute(
        select(S4InventoryItem).order_by(S4InventoryItem.name)
    )).scalars().all()
    checkouts = (await db.execute(
        select(S4Checkout).order_by(desc(S4Checkout.checked_out_at))
    )).scalars().all()

    # Member id → display name map (one query, avoid N+1 in templates).
    member_ids = set()
    for e in expenses:
        member_ids.add(e.member_id); member_ids.add(e.reimbursed_by_id)
    for p in purchases:
        member_ids.add(p.requester_id); member_ids.add(p.approved_by_id)
    for d in donations:
        member_ids.add(d.donor_id); member_ids.add(d.reviewed_by_id)
    for c in checkouts:
        member_ids.add(c.member_id); member_ids.add(c.checked_out_by_id); member_ids.add(c.checked_in_by_id)
    member_ids.discard(None)
    names: dict[int, str] = {}
    if member_ids:
        for m in (await db.execute(select(Member).where(Member.id.in_(member_ids)))).scalars().all():
            names[m.id] = _display_name(m)

    # Active members for the meal-plan assignee dropdown.
    members = (await db.execute(
        select(Member).where(Member.status == "active").order_by(Member.last_name, Member.first_name)
    )).scalars().all()
    for m in members:
        names.setdefault(m.id, _display_name(m))

    return templates.TemplateResponse("pages/s4_dashboard.html", {
        "request": request,
        "user": user,
        "member": member,
        "can_approve": can_approve,
        "events": events,
        "plans": plans,
        "headcounts": headcounts,
        "expenses": expenses,
        "purchases": purchases,
        "donations": donations,
        "inventory": inventory,
        "checkouts": checkouts,
        "members": members,
        "names": names,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 1. Meals & Headcount
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/meals/{event_id}")
@require_auth
async def save_meal_plan(request: Request, event_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_view(user):
        return _denied()
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()

    event = await _get_event_or_none(db, event_id)
    if not event:
        return HTMLResponse("<h2>Event not found</h2>", status_code=404)

    form = await request.form()
    plan = (await db.execute(
        select(S4MealPlan).where(S4MealPlan.event_id == event_id)
    )).scalar_one_or_none()
    if plan is None:
        plan = S4MealPlan(event_id=event_id)
        db.add(plan)

    plan.sat_breakfast = form.get("sat_breakfast") == "on"
    plan.sat_lunch = form.get("sat_lunch") == "on"
    plan.sat_dinner = form.get("sat_dinner") == "on"
    plan.sun_breakfast = form.get("sun_breakfast") == "on"
    plan.menu_notes = (form.get("menu_notes") or "").strip() or None
    assigned = (form.get("assigned_to") or "").strip()
    plan.assigned_to_id = int(assigned) if assigned.isdigit() else None

    await db.commit()
    return RedirectResponse(url="/api/s4#meals", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Expense Reimbursement
# ─────────────────────────────────────────────────────────────────────────────

async def _store_receipt(filename: str, data: bytes) -> str | None:
    """Upload a receipt to NC S4 folder; returns the WebDAV path or None."""
    settings = get_settings()
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in filename).strip() or "receipt"
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = f"{NC_S4_BASE}/Receipts/{date_str}_{safe}"
    async with httpx.AsyncClient(timeout=20) as client:
        # Ensure the Receipts folder exists (MKCOL is idempotent).
        folder = f"{NC_S4_BASE}/Receipts"
        await client.request("MKCOL", f"{settings.nc_url}{folder}/", auth=(NC_SVC_USER, NC_SVC_PASS))
        resp = await client.put(
            f"{settings.nc_url}{path}",
            content=data,
            auth=(NC_SVC_USER, NC_SVC_PASS),
        )
        if resp.status_code in (201, 204):
            return path
        log.warning("S4 receipt upload returned %s for %s", resp.status_code, safe)
        return None


@router.post("/expenses")
@require_auth
async def submit_expense(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_view(user):
        return _denied()
    member = await _current_member(request, db)
    if not member:
        return _denied()

    form = await request.form()
    title = (form.get("title") or "").strip()
    amount_raw = (form.get("amount") or "").strip()
    event_raw = (form.get("event_id") or "").strip()
    description = (form.get("description") or "").strip() or None

    def _err(msg: str):
        return HTMLResponse(f'<div class="s4-flash s4-err">{msg}</div>', status_code=400)

    if not title:
        return _err("A title is required.")
    try:
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError
    except ValueError:
        return _err("Enter a valid amount.")

    exp = S4Expense(
        member_id=member.id,
        event_id=int(event_raw) if event_raw.isdigit() else None,
        title=title,
        description=description,
        amount=amount,
        status="pending",
    )

    # Optional receipt upload.
    file = form.get("receipt")
    if isinstance(file, UploadFile) and getattr(file, "filename", None):
        data = await file.read(MAX_RECEIPT_BYTES + 1)
        if len(data) > MAX_RECEIPT_BYTES:
            return _err("Receipt exceeds the 10MB limit.")
        if data:
            mime = (file.content_type or "").strip().lower()
            if mime in RECEIPT_MIMES:
                path = await _store_receipt(file.filename or "receipt", data)
                if path:
                    exp.receipt_url = path
            else:
                # Fall back to a generic extension but still store.
                path = await _store_receipt((file.filename or "receipt"), data)
                if path:
                    exp.receipt_url = path

    db.add(exp)
    await db.commit()
    await db.refresh(exp)

    # Notify S4 head + Command.
    await _notify_approvers(db, "📄 Expense submitted", f"{_display_name(member)} submitted \"{title}\" (${amount:.2f}).", "/api/s4#expenses")

    return RedirectResponse(url="/api/s4#expenses", status_code=302)


async def _notify_approvers(db: AsyncSession, title: str, body: str, link: str):
    from app.routes.notifications import create_notification
    rows = (await db.execute(select(Member).where(Member.status == "active"))).scalars().all()
    notified: set[int] = set()
    for m in rows:
        # Command members carry 'command' or 'admin' role; S4 head carries the lead billet.
        roles = set((m.portal_roles or "").split(",")) if m.portal_roles else set()
        is_command = bool(roles & {"command", "admin"})
        is_s4_head = _is_s4_head(m)
        if (is_command or is_s4_head) and m.id not in notified:
            notified.add(m.id)
            await create_notification(db, m.id, "shop", f"📦 {title}", body=body, link=link, icon="📦")
    await db.commit()


@router.post("/expenses/{expense_id}/approve")
@require_auth
async def approve_expense(request: Request, expense_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    exp = (await db.execute(select(S4Expense).where(S4Expense.id == expense_id))).scalar_one_or_none()
    if not exp:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    exp.status = "approved"
    await db.commit()
    return RedirectResponse(url="/api/s4#expenses", status_code=302)


@router.post("/expenses/{expense_id}/reimburse")
@require_auth
async def reimburse_expense(request: Request, expense_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    exp = (await db.execute(select(S4Expense).where(S4Expense.id == expense_id))).scalar_one_or_none()
    if not exp:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    exp.status = "reimbursed"
    exp.reimbursed_at = datetime.now(timezone.utc)
    exp.reimbursed_by_id = member.id
    await db.commit()
    return RedirectResponse(url="/api/s4#expenses", status_code=302)


@router.post("/expenses/{expense_id}/reject")
@require_auth
async def reject_expense(request: Request, expense_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    exp = (await db.execute(select(S4Expense).where(S4Expense.id == expense_id))).scalar_one_or_none()
    if not exp:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    exp.status = "rejected"
    await db.commit()
    return RedirectResponse(url="/api/s4#expenses", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Purchase Requests
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/purchases")
@require_auth
async def submit_purchase(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_view(user):
        return _denied()
    member = await _current_member(request, db)
    if not member:
        return _denied()

    form = await request.form()
    item_name = (form.get("item_name") or "").strip()
    cost_raw = (form.get("estimated_cost") or "").strip()
    qty_raw = (form.get("quantity") or "1").strip()
    justification = (form.get("justification") or "").strip()

    def _err(msg: str):
        return HTMLResponse(f'<div class="s4-flash s4-err">{msg}</div>', status_code=400)

    if not item_name or not justification:
        return _err("Item name and justification are required.")
    try:
        cost = float(cost_raw)
        if cost <= 0:
            raise ValueError
        qty = int(qty_raw)
        if qty < 1:
            raise ValueError
    except ValueError:
        return _err("Enter a valid cost and quantity.")

    pr = S4PurchaseRequest(
        requester_id=member.id,
        item_name=item_name,
        url=(form.get("url") or "").strip() or None,
        estimated_cost=cost,
        quantity=qty,
        justification=justification,
        status="pending",
    )
    db.add(pr)
    await db.commit()
    await db.refresh(pr)

    await _notify_approvers(db, "Purchase request", f"{_display_name(member)} requested {qty}× {item_name} (${cost:.2f}).", "/api/s4#purchases")

    return RedirectResponse(url="/api/s4#purchases", status_code=302)


@router.post("/purchases/{pr_id}/approve")
@require_auth
async def approve_purchase(request: Request, pr_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    pr = (await db.execute(select(S4PurchaseRequest).where(S4PurchaseRequest.id == pr_id))).scalar_one_or_none()
    if not pr:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    pr.status = "approved"
    pr.approved_by_id = member.id
    pr.approved_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url="/api/s4#purchases", status_code=302)


@router.post("/purchases/{pr_id}/deny")
@require_auth
async def deny_purchase(request: Request, pr_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    pr = (await db.execute(select(S4PurchaseRequest).where(S4PurchaseRequest.id == pr_id))).scalar_one_or_none()
    if not pr:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    form = await request.form()
    pr.status = "denied"
    pr.denial_reason = (form.get("denial_reason") or "").strip() or None
    await db.commit()
    return RedirectResponse(url="/api/s4#purchases", status_code=302)


@router.post("/purchases/{pr_id}/advance")
@require_auth
async def advance_purchase(request: Request, pr_id: int, db: AsyncSession = Depends(get_db)):
    """Advance an approved purchase: purchased → received (and into inventory if named)."""
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    pr = (await db.execute(select(S4PurchaseRequest).where(S4PurchaseRequest.id == pr_id))).scalar_one_or_none()
    if not pr:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    if pr.status == "approved":
        pr.status = "purchased"
        pr.purchased_at = datetime.now(timezone.utc)
    elif pr.status == "purchased":
        pr.status = "received"
        pr.received_at = datetime.now(timezone.utc)
        # Auto-create an inventory item so received gear lands on the books.
        db.add(S4InventoryItem(
            name=pr.item_name,
            category="Purchased",
            description=pr.justification,
            source_type="purchase",
            source_purchase_id=pr.id,
            status="available",
        ))
    await db.commit()
    return RedirectResponse(url="/api/s4#purchases", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Equipment Donations
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/donations")
@require_auth
async def submit_donation(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not _can_view(user):
        return _denied()
    member = await _current_member(request, db)
    if not member:
        return _denied()

    form = await request.form()
    item_name = (form.get("item_name") or "").strip()
    condition = (form.get("condition") or "Good").strip()
    qty_raw = (form.get("quantity") or "1").strip()

    def _err(msg: str):
        return HTMLResponse(f'<div class="s4-flash s4-err">{msg}</div>', status_code=400)

    if not item_name:
        return _err("Item name is required.")
    try:
        qty = int(qty_raw)
        if qty < 1:
            raise ValueError
    except ValueError:
        return _err("Enter a valid quantity.")

    dn = S4EquipmentDonation(
        donor_id=member.id,
        item_name=item_name,
        description=(form.get("description") or "").strip() or None,
        condition=condition,
        quantity=qty,
        status="submitted",
    )
    db.add(dn)
    await db.commit()
    await db.refresh(dn)

    await _notify_approvers(db, "Equipment donation", f"{_display_name(member)} offered {qty}× {item_name} ({condition}).", "/api/s4#donations")

    return RedirectResponse(url="/api/s4#donations", status_code=302)


@router.post("/donations/{dn_id}/accept")
@require_auth
async def accept_donation(request: Request, dn_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    dn = (await db.execute(select(S4EquipmentDonation).where(S4EquipmentDonation.id == dn_id))).scalar_one_or_none()
    if not dn:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    dn.status = "accepted_pending_dropoff"
    dn.reviewed_by_id = member.id
    dn.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url="/api/s4#donations", status_code=302)


@router.post("/donations/{dn_id}/received")
@require_auth
async def receive_donation(request: Request, dn_id: int, db: AsyncSession = Depends(get_db)):
    """Mark a donation physically received; create inventory item(s)."""
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    dn = (await db.execute(select(S4EquipmentDonation).where(S4EquipmentDonation.id == dn_id))).scalar_one_or_none()
    if not dn:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    dn.status = "received"
    dn.received_at = datetime.now(timezone.utc)
    db.add(S4InventoryItem(
        name=dn.item_name,
        category="Donated",
        description=dn.description,
        condition=dn.condition,
        source_type="donation",
        source_donation_id=dn.id,
        status="available",
    ))
    await db.commit()
    return RedirectResponse(url="/api/s4#donations", status_code=302)


@router.post("/donations/{dn_id}/reject")
@require_auth
async def reject_donation(request: Request, dn_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    dn = (await db.execute(select(S4EquipmentDonation).where(S4EquipmentDonation.id == dn_id))).scalar_one_or_none()
    if not dn:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    dn.status = "rejected"
    dn.reviewed_by_id = member.id
    dn.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url="/api/s4#donations", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Inventory + Possession Log
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/inventory")
@require_auth
async def add_inventory_item(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()

    form = await request.form()
    name = (form.get("name") or "").strip()
    category = (form.get("category") or "").strip()

    def _err(msg: str):
        return HTMLResponse(f'<div class="s4-flash s4-err">{msg}</div>', status_code=400)

    if not name or not category:
        return _err("Name and category are required.")

    item = S4InventoryItem(
        name=name,
        category=category,
        description=(form.get("description") or "").strip() or None,
        serial_number=(form.get("serial_number") or "").strip() or None,
        condition=(form.get("condition") or "Good").strip(),
        location=(form.get("location") or "").strip() or None,
        status="available",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return RedirectResponse(url="/api/s4#inventory", status_code=302)


@router.post("/inventory/{item_id}/edit")
@require_auth
async def edit_inventory_item(request: Request, item_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    item = (await db.execute(select(S4InventoryItem).where(S4InventoryItem.id == item_id))).scalar_one_or_none()
    if not item:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    form = await request.form()
    item.name = (form.get("name") or item.name).strip() or item.name
    item.category = (form.get("category") or item.category).strip() or item.category
    item.description = (form.get("description") or "").strip() or None
    item.serial_number = (form.get("serial_number") or "").strip() or None
    item.condition = (form.get("condition") or item.condition).strip()
    item.location = (form.get("location") or "").strip() or None
    await db.commit()
    return RedirectResponse(url="/api/s4#inventory", status_code=302)


@router.post("/inventory/{item_id}/retire")
@require_auth
async def retire_inventory_item(request: Request, item_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    item = (await db.execute(select(S4InventoryItem).where(S4InventoryItem.id == item_id))).scalar_one_or_none()
    if not item:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    item.status = "retired"
    await db.commit()
    return RedirectResponse(url="/api/s4#inventory", status_code=302)


@router.get("/inventory/{item_id}/qr", response_class=Response)
@require_auth
async def inventory_qr(request: Request, item_id: int, db: AsyncSession = Depends(get_db)):
    """Return a QR PNG encoding this item's check-in/out URL."""
    user = get_current_user(request)
    if not _can_view(user):
        return _denied()
    item = (await db.execute(select(S4InventoryItem).where(S4InventoryItem.id == item_id))).scalar_one_or_none()
    if not item:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    qr_url = f"{get_settings().app_url}/api/s4/inventory/{item_id}/checkout"
    img = qrcode.make(qr_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")


@router.get("/inventory/{item_id}/checkout", response_class=HTMLResponse)
@require_auth
async def checkout_page(request: Request, item_id: int, db: AsyncSession = Depends(get_db)):
    """Mobile-friendly checkout/check-in page (what a scanned QR lands on)."""
    user = get_current_user(request)
    if not _can_view(user):
        return _denied()
    member = await _current_member(request, db)
    item = (await db.execute(select(S4InventoryItem).where(S4InventoryItem.id == item_id))).scalar_one_or_none()
    if not item:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    # Current open checkout for this item, if any.
    open_co = (await db.execute(
        select(S4Checkout).where(
            S4Checkout.item_id == item_id,
            S4Checkout.checked_in_at.is_(None),
        ).order_by(desc(S4Checkout.checked_out_at))
    )).scalars().first()

    members = (await db.execute(
        select(Member).where(Member.status == "active").order_by(Member.last_name, Member.first_name)
    )).scalars().all()
    names: dict[int, str] = {m.id: _display_name(m) for m in members}

    return templates.TemplateResponse("pages/s4_checkout.html", {
        "request": request,
        "user": user,
        "member": member,
        "item": item,
        "open_checkout": open_co,
        "members": members,
        "names": names,
        "can_approve": _can_approve(user, member),
    })


@router.post("/inventory/{item_id}/checkout")
@require_auth
async def checkout_item(request: Request, item_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    item = (await db.execute(select(S4InventoryItem).where(S4InventoryItem.id == item_id))).scalar_one_or_none()
    if not item:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)

    form = await request.form()
    holder_raw = (form.get("member_id") or "").strip()
    if not holder_raw.isdigit():
        return HTMLResponse('<div class="s4-flash s4-err">Select who is taking the item.</div>', status_code=400)
    holder_id = int(holder_raw)

    co = S4Checkout(
        item_id=item_id,
        member_id=holder_id,
        checked_out_by_id=member.id,
        checked_out_at=datetime.now(timezone.utc),
    )
    db.add(co)
    item.status = "checked_out"
    await db.commit()
    return RedirectResponse(url=f"/api/s4/inventory/{item_id}/checkout", status_code=302)


@router.post("/inventory/{item_id}/checkin")
@require_auth
async def checkin_item(request: Request, item_id: int, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    member = await _current_member(request, db)
    if not _can_approve(user, member):
        return _denied()
    item = (await db.execute(select(S4InventoryItem).where(S4InventoryItem.id == item_id))).scalar_one_or_none()
    if not item:
        return HTMLResponse("<h2>Not found</h2>", status_code=404)
    open_co = (await db.execute(
        select(S4Checkout).where(
            S4Checkout.item_id == item_id,
            S4Checkout.checked_in_at.is_(None),
        ).order_by(desc(S4Checkout.checked_out_at))
    )).scalars().first()
    if open_co:
        form = await request.form()
        open_co.checked_in_at = datetime.now(timezone.utc)
        open_co.checked_in_by_id = member.id
        open_co.return_condition = (form.get("return_condition") or "").strip() or None
    item.status = "available"
    await db.commit()
    return RedirectResponse(url=f"/api/s4/inventory/{item_id}/checkout", status_code=302)
