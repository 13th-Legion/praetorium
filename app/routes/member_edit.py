"""Member profile editing — Command/S1 can edit any member's profile."""

import logging
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member
from app.models.rank_history import RankHistory
from app.models.conduct import ConductViolation
from config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/members", tags=["member-edit"])
templates = Jinja2Templates(directory="app/templates")

from app.constants import S1_ROLES as EDIT_ROLES, RANK_CHOICES as RANK_OPTIONS, STATUS_OPTIONS, TEAM_OPTIONS, LEADERSHIP_TITLES
from app.geo import assign_zone, geocode_zip
from app.settings import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM,
    NC_SVC_USER, NC_SVC_PASS,
)

BILLET_OPTIONS = [
    ("S1: Administration (Lead)", "S1 — Administration (Lead)"),
    ("S1: Administration", "S1 — Administration"),
    ("S1: Recruiting", "S1 — Recruiting"),
    ("S1: Media/PAO", "S1 — Media / PAO"),
    ("S1: Chaplain", "S1 — Chaplain"),
    ("S2: Intel & Security (Lead)", "S2 — Intel & Security (Lead)"),
    ("S2: Intel & Security", "S2 — Intel & Security"),
    ("S3: Training & Ops (Lead)", "S3 — Training & Ops (Lead)"),
    ("S3: Training & Ops", "S3 — Training & Ops"),
    ("S4: Logistics (Lead)", "S4 — Logistics (Lead)"),
    ("S4: Logistics", "S4 — Logistics"),
    ("S5: Medical (Lead)", "S5 — Medical (Lead)"),
    ("S5: Medical", "S5 — Medical"),
    ("S6: Communications (Lead)", "S6 — Communications (Lead)"),
    ("S6: Communications", "S6 — Communications"),
]


def _can_edit(user: dict) -> bool:
    roles = set(user.get("roles", []))
    return bool(roles & EDIT_ROLES)


# Rank grade → NC rank group mapping
RANK_GROUPS = {
    "E-1": "Rank - Recruit",
    "E-2": "Rank - Enlisted",
    "E-3": "Rank - Enlisted",
    "E-4": "Rank - Enlisted",
    "E-5": "Rank - NCO",
    "E-6": "Rank - NCO",
    "E-7": "Rank - NCO",
    "E-8": "Rank - NCO",
    "E-9": "Rank - NCO",
    "W-1": "Rank - Officer",
    "O-1": "Rank - Officer",
    "O-2": "Rank - Officer",
    "O-3": "Rank - Officer",
    "O-4": "Rank - Officer",
}

ALL_RANK_NC_GROUPS = {"Rank - Recruit", "Rank - Enlisted", "Rank - NCO", "Rank - Officer"}


async def _sync_leadership_groups(username: str, leadership_title: str | None, team: str | None):
    """Sync NC group membership based on leadership title.

    - Add to 'Leaders' group if TL/ATL/CO/XO/1SG/PltSGT, remove if cleared.
    - Add to correct 'Team-{name}' group, remove from old team groups.
    """
    if not username:
        return

    nc_url = settings.nc_url
    auth = (settings.nc_api_user, settings.nc_api_password)
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}

    leader_titles = {"Team Leader", "Assistant Team Leader", "Commanding Officer",
                     "Executive Officer", "First Sergeant", "Platoon Sergeant, Training NCO"}
    is_leader = leadership_title in leader_titles

    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{nc_url}/ocs/v2.php/cloud/users/{username}",
                auth=auth, headers=headers, timeout=10,
            )
            r.raise_for_status()
            current_groups = set(r.json().get("ocs", {}).get("data", {}).get("groups", []))
        except Exception as e:
            log.error(f"Failed to fetch NC groups for {username}: {e}")
            return

        # Leaders group
        if is_leader and "Leaders" not in current_groups:
            try:
                await client.post(
                    f"{nc_url}/ocs/v2.php/cloud/users/{username}/groups",
                    auth=auth, headers=headers,
                    data={"groupid": "Leaders"}, timeout=10,
                )
                log.info(f"Added {username} to Leaders group")
            except Exception as e:
                log.error(f"Failed to add {username} to Leaders: {e}")
        elif not is_leader and "Leaders" in current_groups:
            try:
                await client.delete(
                    f"{nc_url}/ocs/v2.php/cloud/users/{username}/groups",
                    auth=auth, headers=headers,
                    data={"groupid": "Leaders"}, timeout=10,
                )
                log.info(f"Removed {username} from Leaders group")
            except Exception as e:
                log.error(f"Failed to remove {username} from Leaders: {e}")

        # Team group sync
        all_team_groups = {g for g in current_groups if g.startswith("Team-") or g.startswith("Team - ")}
        target_team_group = f"Team-{team}" if team and team != "Headquarters" else None

        # Remove from old team groups (except target)
        for g in all_team_groups:
            if g != target_team_group:
                try:
                    await client.delete(
                        f"{nc_url}/ocs/v2.php/cloud/users/{username}/groups",
                        auth=auth, headers=headers,
                        data={"groupid": g}, timeout=10,
                    )
                    log.info(f"Removed {username} from {g}")
                except Exception as e:
                    log.error(f"Failed to remove {username} from {g}: {e}")

        # Add to target team group
        if target_team_group and target_team_group not in current_groups:
            try:
                await client.post(
                    f"{nc_url}/ocs/v2.php/cloud/users/{username}/groups",
                    auth=auth, headers=headers,
                    data={"groupid": target_team_group}, timeout=10,
                )
                log.info(f"Added {username} to {target_team_group}")
            except Exception as e:
                log.error(f"Failed to add {username} to {target_team_group}: {e}")


async def _sync_rank_group(username: str, new_rank: str):
    """Move a member to the correct NC rank group based on their new rank grade."""
    target_group = RANK_GROUPS.get(new_rank)
    if not target_group or not username:
        return

    nc_url = settings.nc_url
    auth = (settings.nc_api_user, settings.nc_api_password)
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}

    async with httpx.AsyncClient() as client:
        # Get current groups
        try:
            r = await client.get(
                f"{nc_url}/ocs/v2.php/cloud/users/{username}",
                auth=auth, headers=headers, timeout=10,
            )
            r.raise_for_status()
            current_groups = set(r.json().get("ocs", {}).get("data", {}).get("groups", []))
        except Exception as e:
            log.error(f"Failed to fetch NC groups for {username}: {e}")
            return

        # Remove from all other rank groups
        for group in ALL_RANK_NC_GROUPS - {target_group}:
            if group in current_groups:
                try:
                    await client.delete(
                        f"{nc_url}/ocs/v2.php/cloud/users/{username}/groups",
                        auth=auth, headers=headers,
                        data={"groupid": group}, timeout=10,
                    )
                except Exception as e:
                    log.error(f"Failed to remove {username} from {group}: {e}")

        # Add to target rank group if not already in it
        if target_group not in current_groups:
            try:
                await client.post(
                    f"{nc_url}/ocs/v2.php/cloud/users/{username}/groups",
                    auth=auth, headers=headers,
                    data={"groupid": target_group}, timeout=10,
                )
            except Exception as e:
                log.error(f"Failed to add {username} to {target_group}: {e}")


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s or s.strip() == "":
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None




async def _sync_nc_displayname(nc_username: str, display_name: str):
    """Update the Nextcloud display name to match Praetorium rank/name/callsign."""
    if not nc_username or not display_name:
        return
    try:
        nc_url = settings.nc_url
        auth = (settings.nc_api_user, settings.nc_api_password)
        headers = {"OCS-APIRequest": "true", "Accept": "application/json"}

        async with httpx.AsyncClient() as client:
            r = await client.put(
                f"{nc_url}/ocs/v2.php/cloud/users/{nc_username}",
                auth=auth, headers=headers, timeout=10,
                data={"key": "displayname", "value": display_name},
            )
            code = r.json().get("ocs", {}).get("meta", {}).get("statuscode")
            if code == 200:
                log.info(f"NC display name synced: {nc_username} → {display_name}")
            else:
                log.warning(f"NC display name sync failed for {nc_username}: {code}")
    except Exception as e:
        log.error(f"NC display name sync error for {nc_username}: {e}")

@router.get("/{member_id}/edit")
@require_auth
async def edit_member_page(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Edit form for a member's profile."""
    user = get_current_user(request)
    if not _can_edit(user):
        raise HTTPException(status_code=403, detail="Command or S1 access required")

    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Parse current billets into a set for checkbox pre-selection
    current_billets = set()
    if member.primary_billet:
        current_billets = {b.strip() for b in member.primary_billet.split(", ")}

    return templates.TemplateResponse("pages/member_edit.html", {
        "request": request,
        "user": user,
        "member": member,
        "rank_options": RANK_OPTIONS,
        "status_options": STATUS_OPTIONS,
        "team_options": TEAM_OPTIONS,
        "billet_options": BILLET_OPTIONS,
        "current_billets": current_billets,
        "leadership_titles": LEADERSHIP_TITLES,
    })


@router.post("/{member_id}/edit")
@require_auth
async def save_member_edit(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Save edits to a member's profile."""
    user = get_current_user(request)
    if not _can_edit(user):
        raise HTTPException(status_code=403, detail="Command or S1 access required")

    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    form = await request.form()

    # Identity
    member.first_name = form.get("first_name", member.first_name).strip()
    member.last_name = form.get("last_name", member.last_name).strip()
    member.callsign = form.get("callsign", "").strip() or None
    member.email = form.get("email", "").strip() or None

    # Assignment — track old rank for promotion logic
    old_rank = member.rank_grade
    member.rank_grade = form.get("rank_grade", member.rank_grade)
    member.status = form.get("status", member.status)
    member.team = form.get("team", "").strip() or None
    member.leadership_title = form.get("leadership_title", "").strip() or None
    selected_billets = form.getlist("billets")
    member.primary_billet = ", ".join(selected_billets) if selected_billets else None

    # Service record
    member.join_date = _parse_date(form.get("join_date"))
    member.patch_date = _parse_date(form.get("patch_date"))
    # Non-promotable — restricted to Command/Admin only
    user = get_current_user(request)
    editor_roles = set(user.get("roles", []))
    if editor_roles & {"command", "admin", "s1"}:
        old_np_until = member.non_promotable_until
        old_np_reason = member.non_promotable_reason
        new_np_until = _parse_date(form.get("non_promotable_until"))
        new_np_reason = form.get("non_promotable_reason", "").strip() or None
        member.non_promotable_until = new_np_until
        member.non_promotable_reason = new_np_reason

        # Auto-log CoC violation when non-promotable is SET (not when cleared)
        if new_np_until and new_np_reason and (new_np_until != old_np_until or new_np_reason != old_np_reason):
            db.add(ConductViolation(
                member_id=member_id,
                violation_date=date.today(),
                reason=new_np_reason,
                action_taken="Non-Promotable",
                start_date=date.today(),
                end_date=new_np_until,
                duration_days=(new_np_until - date.today()).days if new_np_until else None,
                issued_by=user.get("username", "unknown"),
                notes="Auto-recorded from profile edit",
            ))
            log.info(f"CoC violation auto-logged for member {member_id}: Non-Promotable until {new_np_until}")
    member.is_founder = form.get("is_founder") == "on"
    member.is_veteran = form.get("is_veteran") == "on"
    member.mos = form.get("mos", "").strip() or None

    # Contact
    member.phone = form.get("phone", "").strip() or None
    member.address = form.get("address", "").strip() or None
    member.city = form.get("city", "").strip() or None
    member.state = form.get("state", "TX").strip()
    member.zip_code = form.get("zip_code", "").strip() or None
    member.personal_email = form.get("personal_email", "").strip() or None
    member.emergency_contact = form.get("emergency_contact", "").strip() or None
    member.emergency_phone = form.get("emergency_phone", "").strip() or None

    # Radio
    member.ham_callsign = form.get("ham_callsign", "").strip() or None
    member.ham_license_class = form.get("ham_license_class", "").strip() or None
    member.gmrs_callsign = form.get("gmrs_callsign", "").strip() or None

    # --- Geo team auto-recalculation on address change ---
    new_zip = member.zip_code
    old_team = form.get("team", "").strip() or None  # what was submitted in the form
    from app.geo import geocode_address
    
    new_address = member.address
    new_city = member.city
    new_state = member.state
    new_zip = member.zip_code
    
    full_addr = f"{new_address}, {new_city}, {new_state} {new_zip}".strip()
    old_team = form.get("team", "").strip() or None  # what was submitted in the form
    
    if new_zip:
        try:
            lat, lon = None, None
            if new_address and new_city and new_state:
                lat, lon = geocode_address(full_addr)
            if lat is None:
                lat, lon = geocode_zip(new_zip)
                
            if lat is not None:
                member.latitude = lat
                member.longitude = lon
                geo_team, bearing = assign_zone(lat, lon)
                if geo_team != old_team:
                    log.info(f"Geo-reassigned {member.first_name} {member.last_name}: "
                             f"{old_team} → {geo_team} (address {full_addr}, bearing {bearing:.1f}°)")
                    member.team = geo_team
        except Exception as e:
            log.warning(f"Geo-recalculation failed for {member.last_name}: {e}")

    # --- Promotion automation ---
    new_rank = member.rank_grade

    # Auto-set patch_date when promoted from E-1 to E-2+
    if old_rank == "E-1" and new_rank and new_rank != "E-1":
        if not member.patch_date:
            member.patch_date = date.today()
        if member.status == "recruit":
            member.status = "active"

    # Sync NC rank group and display name if rank changed
    if new_rank != old_rank and member.nc_username:
        await _sync_rank_group(member.nc_username, new_rank)
        await _sync_nc_displayname(member.nc_username, member.display_name)

    # Log rank change to history
    if new_rank != old_rank:
        db.add(RankHistory(
            member_id=member_id,
            old_rank=old_rank,
            new_rank=new_rank,
            changed_by=user.get("username"),
        ))

    # --- Leadership cascade ---
    # If setting TL, clear previous TL — but HQ TL and geo team TL are separate roles.
    # Only enforce uniqueness within the same is_hq scope.
    new_title = member.leadership_title
    new_team = member.team
    member_is_hq = getattr(member, "is_hq", False)

    if new_title == "Team Leader" and new_team:
        q = select(Member).where(
            Member.team == new_team,
            Member.leadership_title == "Team Leader",
            Member.id != member_id,
            Member.status.in_(("active", "recruit")),
            Member.is_hq == member_is_hq,  # only match same scope (HQ vs geo)
        )
        result2 = await db.execute(q)
        old_tl = result2.scalar_one_or_none()
        if old_tl:
            scope = "HQ" if member_is_hq else new_team
            log.info(f"Clearing TL from {old_tl.first_name} {old_tl.last_name} (was {scope} TL)")
            old_tl.leadership_title = None
            old_tl.updated_at = datetime.utcnow()
            if old_tl.nc_username:
                await _sync_leadership_groups(old_tl.nc_username, None, old_tl.team)

    # Same for ATL
    if new_title == "Assistant Team Leader" and new_team:
        q = select(Member).where(
            Member.team == new_team,
            Member.leadership_title == "Assistant Team Leader",
            Member.id != member_id,
            Member.status.in_(("active", "recruit")),
            Member.is_hq == member_is_hq,
        )
        result3 = await db.execute(q)
        old_atl = result3.scalar_one_or_none()
        if old_atl:
            scope = "HQ" if member_is_hq else new_team
            log.info(f"Clearing ATL from {old_atl.first_name} {old_atl.last_name} (was {scope} ATL)")
            old_atl.leadership_title = None
            old_atl.updated_at = datetime.utcnow()
            if old_atl.nc_username:
                await _sync_leadership_groups(old_atl.nc_username, None, old_atl.team)

    # Sync current member's leadership + team groups in NC
    if member.nc_username:
        await _sync_leadership_groups(member.nc_username, member.leadership_title, member.team)

    member.updated_at = datetime.utcnow()
    await db.commit()

    # Redirect back to profile
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/profile/{member_id}", status_code=303)


# ─── Resend NC/Portal credentials (PP-223) ────────────────────────────────────
# Decoupled from the Deck recruit-pipeline: resets the member's Nextcloud
# password and emails fresh credentials, keyed off the member record. Closes
# the gap where hand-provisioned / legacy accounts never got a welcome email.

def _build_credentials_email(first_name: str, nc_username: str, temp_password: str, to_email: str):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    subject = "13th Legion — Your Nextcloud & Portal Access"
    html_body = f"""<div style="font-family:sans-serif;max-width:600px;">
    <h2 style="color:#d4a537;">Welcome to the 13th Legion</h2>
    <p>Welcome to the 13th Legion digital infrastructure, {first_name}!</p>
    <p>Your Nextcloud account is ready. This is where we manage files, calendars, tasks, and comms for the unit.</p>
    <div style="background:#f5f5f5;padding:16px;border-radius:8px;margin:16px 0;">
        <p style="margin:4px 0;"><strong>Nextcloud:</strong> <a href="https://cloud.13thlegion.org">cloud.13thlegion.org</a></p>
        <p style="margin:4px 0;"><strong>Username:</strong> <code>{nc_username}</code></p>
        <p style="margin:4px 0;"><strong>Temporary Password:</strong> <code>{temp_password}</code></p>
        <p style="margin:4px 0;"><strong>Portal:</strong> <a href="https://portal.13thlegion.org">portal.13thlegion.org</a></p>
        <p style="margin:4px 0;font-size:12px;color:#666;">(Portal uses the same Nextcloud login)</p>
    </div>
    <p><strong>First steps:</strong></p>
    <ol>
        <li>Log in to Nextcloud and <strong>change your password</strong> (Settings &rarr; Security)</li>
        <li>Set up <strong>2FA</strong> (Settings &rarr; Security &rarr; TOTP)</li>
        <li>Install the <strong>Nextcloud app</strong> on your phone for notifications</li>
        <li>Log in to the <strong>Portal</strong> to see your profile, training record, and upcoming events</li>
    </ol>
    <p>If you have any issues, reach out to Cav or Archer.</p>
    <p>V/R,<br>13th Legion S6</p>
</div>"""
    text_body = f"""Welcome to the 13th Legion, {first_name}!

Your Nextcloud account is ready.

Nextcloud: https://cloud.13thlegion.org
Username: {nc_username}
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
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    return msg


@router.post("/{member_id}/resend-credentials")
@require_auth
async def resend_credentials(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Reset the member's NC password and email fresh credentials. S1/Command/Admin."""
    import secrets, string, smtplib

    user = get_current_user(request)
    if not _can_edit(user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    member = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if not member.nc_username:
        raise HTTPException(status_code=400, detail="Member has no Nextcloud username")
    if not member.email:
        raise HTTPException(status_code=400, detail="Member has no email on record")

    temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))

    # 1. Reset NC password
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.put(
                f"{settings.nc_url}/ocs/v2.php/cloud/users/{member.nc_username}",
                auth=(NC_SVC_USER, NC_SVC_PASS),
                headers={"OCS-APIRequest": "true"},
                data={"key": "password", "value": temp_password},
            )
        if resp.status_code != 200:
            log.error(f"Resend creds: NC password reset failed for {member.nc_username}: {resp.status_code}")
            raise HTTPException(status_code=502, detail=f"NC password reset failed (HTTP {resp.status_code})")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Resend creds: NC reset error for {member.nc_username}: {e}")
        raise HTTPException(status_code=502, detail=f"NC reset error: {e}")

    # 2. Send the credentials email via Proton Bridge
    msg = _build_credentials_email(member.first_name, member.nc_username, temp_password, member.email)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [member.email], msg.as_string())
        log.info(f"Resent credentials to {member.email} for {member.nc_username} by {user.get('username')}")
    except Exception as e:
        log.error(f"Resend creds: NC reset OK but email failed for {member.email}: {e}")
        raise HTTPException(status_code=502, detail=f"Password was reset but email failed: {e}")

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/api/members/{member_id}/edit?creds_sent=1", status_code=303)
