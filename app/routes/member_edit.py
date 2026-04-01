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
from config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/members", tags=["member-edit"])
templates = Jinja2Templates(directory="app/templates")

from app.constants import S1_ROLES as EDIT_ROLES, RANK_CHOICES as RANK_OPTIONS, STATUS_OPTIONS, TEAM_OPTIONS, LEADERSHIP_TITLES
from app.geo import assign_zone, geocode_zip

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
        member.non_promotable_until = _parse_date(form.get("non_promotable_until"))
        member.non_promotable_reason = form.get("non_promotable_reason", "").strip() or None
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

    # Sync NC rank group if rank changed
    if new_rank != old_rank and member.nc_username:
        await _sync_rank_group(member.nc_username, new_rank)

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
