"""Self-service contact info editing — every member can edit their own."""

from datetime import datetime

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member

router = APIRouter(prefix="/api/profile", tags=["contact-edit"])
templates = Jinja2Templates(directory="app/templates")


async def _get_own_member(request: Request, db: AsyncSession) -> Member:
    """Look up the logged-in user's Member record."""
    user = get_current_user(request)
    result = await db.execute(
        select(Member).where(Member.nc_username == user["username"])
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="No personnel record found for your account.")
    return member


def _render_contact_card(member: Member) -> str:
    """Render the read-only contact info card body HTML (replaces #contact-card-body)."""
    rows = []

    rows.append(_detail_row("Email", member.email or "—"))
    rows.append(_detail_row("Phone", member.phone or "—"))

    if member.address:
        addr_parts = [member.address]
        if member.city:
            addr_parts.append(f", {member.city}")
        if member.state:
            addr_parts.append(f", {member.state}")
        if member.zip_code:
            addr_parts.append(f" {member.zip_code}")
        rows.append(_detail_row("Address", "".join(addr_parts)))

    if member.personal_email:
        rows.append(_detail_row("Personal Email", member.personal_email))

    if member.emergency_contact:
        ec = member.emergency_contact
        if member.emergency_phone:
            ec += f" — {member.emergency_phone}"
        rows.append(
            f'<div class="detail-row" style="margin-top:8px;padding-top:8px;'
            f'border-top:1px solid rgba(255,255,255,0.08);">'
            f'<span class="detail-label">Emergency Contact</span>'
            f'<span>{ec}</span></div>'
        )

    # Radio section
    radio_rows = []
    if member.ham_callsign:
        radio_rows.append(_detail_row("HAM Callsign", member.ham_callsign))
        radio_rows.append(_detail_row("License Class", member.ham_license_class or "—"))
    if member.gmrs_callsign:
        radio_rows.append(_detail_row("GMRS Callsign", member.gmrs_callsign))

    if radio_rows:
        rows.append(
            '<div style="margin-top:8px;padding-top:8px;'
            'border-top:1px solid rgba(255,255,255,0.08);">'
            '<div style="font-size:11px;font-weight:700;color:#d4a537;margin-bottom:4px;">Radio</div>'
            + "".join(radio_rows) + '</div>'
        )

    # Verified-at timestamp
    if member.contact_verified_at:
        ts = member.contact_verified_at.strftime("%b %d, %Y")
        rows.append(
            f'<div style="margin-top:10px;font-size:11px;color:#666;">'
            f'✓ Last verified {ts}</div>'
        )

    body = "".join(rows)

    return (
        f'<div id="contact-card-body" class="card-body">{body}</div>'
    )


def _detail_row(label: str, value: str) -> str:
    return (
        f'<div class="detail-row">'
        f'<span class="detail-label">{label}</span>'
        f'<span>{value}</span></div>'
    )


@router.get("/contact-edit")
@require_auth
async def contact_edit_form(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the inline contact edit form (HTMX partial)."""
    member = await _get_own_member(request, db)
    return templates.TemplateResponse("partials/contact_edit.html", {
        "request": request,
        "member": member,
    })


@router.get("/contact-card")
@require_auth
async def contact_card_readonly(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the read-only contact card body (for cancel / post-save)."""
    member = await _get_own_member(request, db)
    return HTMLResponse(_render_contact_card(member))


@router.post("/contact")
@require_auth
async def save_contact(request: Request, db: AsyncSession = Depends(get_db)):
    """Save self-service contact info edits."""
    member = await _get_own_member(request, db)
    form = await request.form()

    # Contact fields
    member.phone = form.get("phone", "").strip() or None
    member.address = form.get("address", "").strip() or None
    member.city = form.get("city", "").strip() or None
    member.state = form.get("state", "TX").strip().upper() or "TX"
    member.zip_code = form.get("zip_code", "").strip() or None
    member.personal_email = form.get("personal_email", "").strip() or None
    member.emergency_contact = form.get("emergency_contact", "").strip() or None
    member.emergency_phone = form.get("emergency_phone", "").strip() or None

    # Re-geocode if address changed
    from app.geo import geocode_address, geocode_zip, assign_zone
    new_addr = f"{member.address}, {member.city}, {member.state} {member.zip_code}".strip()
    try:
        lat, lon = None, None
        if member.address and member.city and member.state:
            lat, lon = geocode_address(new_addr)
        if lat is None and member.zip_code:
            lat, lon = geocode_zip(member.zip_code)
            
        if lat is not None:
            member.latitude = lat
            member.longitude = lon
            geo_team, bearing = assign_zone(lat, lon)
            if geo_team != member.team and geo_team:
                member.team = geo_team
                import logging
                log = logging.getLogger(__name__)
                log.info(f"Contact edit: Geo-reassigned {member.first_name} {member.last_name} to {geo_team} (bearing {bearing:.1f}°)")
    except Exception:
        pass

    # Mark verified
    member.contact_verified_at = datetime.utcnow()
    member.updated_at = datetime.utcnow()

    await db.commit()

    # Return updated read-only card
    await db.refresh(member)
    html = _render_contact_card(member)
    # Inject a brief success flash
    flash = (
        '<div style="background:rgba(46,125,50,0.15);border:1px solid #2e7d32;'
        'border-radius:4px;padding:8px 12px;margin-bottom:12px;font-size:13px;color:#4caf50;">'
        '✅ Contact info updated</div>'
    )
    return HTMLResponse(flash + html)

from fastapi import Form
from config import get_settings
import httpx

@router.post("/change-password")
@require_auth
async def change_password(
    request: Request,
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    """Self-service password change. Updates Nextcloud via API."""
    user = get_current_user(request)
    username = user.get("username")
    
    if not username:
        return HTMLResponse("<div style='color: #ff4444;'>Error: No Nextcloud account linked.</div>")
        
    if new_password != confirm_password:
        return HTMLResponse("<div style='color: #ff4444;'>Error: Passwords do not match.</div>")
        
    if len(new_password) < 8:
        return HTMLResponse("<div style='color: #ff4444;'>Error: Password must be at least 8 characters.</div>")

    settings = get_settings()
    
    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{settings.nc_url}/ocs/v2.php/cloud/users/{username}",
            auth=(settings.nc_api_user, settings.nc_api_password),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            data={"key": "password", "value": new_password},
            timeout=10
        )
        
        if r.status_code == 200:
            return HTMLResponse("<div style='color: #00C851;'>✅ Password updated successfully! You will need to use this new password next time you log in.</div>")
        else:
            return HTMLResponse("<div style='color: #ff4444;'>Error: Failed to communicate with identity server.</div>")
