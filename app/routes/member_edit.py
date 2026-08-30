"""Member profile editing — Command/S1 can edit any member's profile."""

import logging
import re
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

from app.constants import S1_ROLES as EDIT_ROLES, STATUS_OPTIONS, TEAM_OPTIONS, LEADERSHIP_TITLES
from app.services import ranks as _ranks
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
# RANK_GROUPS (grade -> NC group) now comes from the ranks service. The old
# local copy was drifted (missing E-8M, W-2..W-5). Use _ranks.nc_group_map().

ALL_RANK_NC_GROUPS = {"Rank - Recruit", "Rank - Enlisted", "Rank - NCO", "Rank - Officer"}


async def _sync_leadership_groups(username: str, leadership_title: str | None, team: str | None, billets: str | None = None):
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
    is_leader = (leadership_title in leader_titles) or (bool(billets) and "(Lead)" in billets)

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
                await client.request(
                        "DELETE",
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
                    await client.request(
                        "DELETE",
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



def _shop_numbers_from_billets(billets: str | None) -> set[str]:
    """Extract shop numbers (as strings) from a billet string like
    'S1: Administration, S3: Training' -> {'1', '3'}."""
    if not billets:
        return set()
    return set(re.findall(r"S(\d)\s*:", billets))


def _is_shop_lead(billets: str | None, shop_num: str) -> bool:
    """True if the billets grant a Lead role for the given shop number.
    e.g. 'S1: Administration (Lead)' -> lead of shop 1."""
    if not billets:
        return False
    for part in billets.split(","):
        part = part.strip()
        m = re.match(r"S(\d)\s*:", part)
        if m and m.group(1) == shop_num and "(Lead)" in part:
            return True
    return False


async def _sync_shop_groups(username: str, old_billets: str | None, new_billets: str | None):
    """Sync NC [S-n] shop group membership from a member's billets.

    Single source of truth = the portal billet assignment. On save we add the
    member to the [S-n] group for every shop they hold and remove them from
    shops they dropped. [S-n] Lead subgroups are granted only when the billet
    carries '(Lead)' for that shop. Non-shop groups are never touched.

    Queries the live NC group list so no static billet->group map is needed.
    """
    if not username:
        return

    nc_url = settings.nc_url
    auth = (settings.nc_api_user, settings.nc_api_password)
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}

    new_shops = _shop_numbers_from_billets(new_billets)
    old_shops = _shop_numbers_from_billets(old_billets)

    async with httpx.AsyncClient() as client:
        # Live NC group list -> map shop number to actual group names.
        try:
            gr = await client.get(
                f"{nc_url}/ocs/v2.php/cloud/groups",
                auth=auth, headers=headers, timeout=10,
            )
            gr.raise_for_status()
            all_groups = gr.json().get("ocs", {}).get("data", {}).get("groups", [])
        except Exception as e:
            log.error(f"Failed to fetch NC group list for shop sync ({username}): {e}")
            return

        # e.g. {'1': {'base': '[S-1] Admin', 'lead': '[S-1] Lead'}, ...}
        shop_map: dict[str, dict[str, str]] = {}
        for g in all_groups:
            m = re.match(r"\[S-(\d)\]", g)
            if not m:
                continue
            num = m.group(1)
            entry = shop_map.setdefault(num, {})
            if g.rstrip().endswith("Lead"):
                entry["lead"] = g
            else:
                entry["base"] = g

        # Member's current groups.
        try:
            r = await client.get(
                f"{nc_url}/ocs/v2.php/cloud/users/{username}",
                auth=auth, headers=headers, timeout=10,
            )
            r.raise_for_status()
            current_groups = set(r.json().get("ocs", {}).get("data", {}).get("groups", []))
        except Exception as e:
            log.error(f"Failed to fetch NC groups for shop sync ({username}): {e}")
            return

        async def _add(group: str):
            if group and group not in current_groups:
                try:
                    await client.post(
                        f"{nc_url}/ocs/v2.php/cloud/users/{username}/groups",
                        auth=auth, headers=headers,
                        data={"groupid": group}, timeout=10,
                    )
                    log.info(f"Shop sync: added {username} to {group}")
                except Exception as e:
                    log.error(f"Shop sync: failed to add {username} to {group}: {e}")

        async def _remove(group: str):
            if group and group in current_groups:
                try:
                    # httpx .delete() cannot carry a body; use .request("DELETE").
                    await client.request(
                        "DELETE",
                        f"{nc_url}/ocs/v2.php/cloud/users/{username}/groups",
                        auth=auth, headers=headers,
                        data={"groupid": group}, timeout=10,
                    )
                    log.info(f"Shop sync: removed {username} from {group}")
                except Exception as e:
                    log.error(f"Shop sync: failed to remove {username} from {group}: {e}")

        # Add held shops (+ lead subgroup where applicable).
        for num in new_shops:
            entry = shop_map.get(num, {})
            await _add(entry.get("base"))
            if _is_shop_lead(new_billets, num):
                await _add(entry.get("lead"))
            else:
                await _remove(entry.get("lead"))

        # Remove dropped shops (base + lead).
        for num in old_shops - new_shops:
            entry = shop_map.get(num, {})
            await _remove(entry.get("base"))
            await _remove(entry.get("lead"))


async def _sync_rank_group(username: str, new_rank: str):
    """Move a member to the correct NC rank group based on their new rank grade."""
    target_group = _ranks.nc_group_map().get(new_rank)
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
                    await client.request(
                        "DELETE",
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


# Sentinel used to distinguish "field absent from the submitted form" from
# "field present but blank". A partial/broken submit (autofill glitch, JS
# double-submit, a re-rendered form) that omits inputs must NOT null out data
# the user never intended to touch.
_MISSING = object()


def _str_field(form, name: str, current):
    """Return the trimmed submitted value for a text field, or the *current*
    value if the field was not part of the submission.

    - key absent   -> leave unchanged (returns `current`)
    - key present, blank -> explicit clear (returns None)
    - key present, value -> new trimmed value (or None if it trims empty)
    """
    raw = form.get(name, _MISSING)
    if raw is _MISSING:
        return current
    return raw.strip() or None


def _guard_full_form(form) -> bool:
    """Sanity gate: a legitimate full profile-edit submission always carries the
    required identity inputs (first_name/last_name are `required` in the form).
    If those are missing the POST is a partial/corrupt submit and we must NOT
    apply it (it would wipe the record). Returns True when the form looks whole.
    """
    return form.get("first_name") is not None and form.get("last_name") is not None




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

    # Team dropdown: DB-backed team names (single source of truth), so renames
    # persist across restarts. Also include the member's own current team so it
    # never shows blank, and any live DB team values not yet in the table.
    from app.services import teams as _teams
    team_options = await _teams.team_options()
    live_teams = {
        r[0] for r in (await db.execute(
            select(Member.team).where(Member.team.isnot(None)).distinct()
        )).all() if r[0]
    }
    for t in sorted(live_teams | ({member.team} if member.team else set())):
        if t not in team_options:
            team_options.append(t)

    return templates.TemplateResponse("pages/member_edit.html", {
        "request": request,
        "user": user,
        "member": member,
        "rank_options": _ranks.choices(),
        "status_options": STATUS_OPTIONS,
        "team_options": team_options,
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

    # --- Guard against partial/corrupt submissions wiping the record ---
    # The edit form pre-fills every field and marks first_name/last_name as
    # `required`. If those aren't in the POST, this is not a real full-form
    # save (autofill glitch, double-submit, re-render) and applying it would
    # null out data the editor never touched. Abort instead of destroying data.
    if not _guard_full_form(form):
        log.warning(
            f"Rejected partial member-edit POST for id={member_id} "
            f"by {user.get('username')}: missing required identity fields "
            f"(keys={list(form.keys())})"
        )
        raise HTTPException(
            status_code=400,
            detail="Incomplete form submission — no changes were saved. "
                   "Please reload the edit page and try again.",
        )

    # Identity — only overwrite when the field is present in the submission.
    member.first_name = (form.get("first_name") or member.first_name).strip()
    member.last_name = (form.get("last_name") or member.last_name).strip()
    member.callsign = _str_field(form, "callsign", member.callsign)
    member.email = _str_field(form, "email", member.email)

    # Assignment — track old rank for promotion logic
    old_rank = member.rank_grade
    old_billets = member.primary_billet
    prev_team = member.team  # team stored before this save (for override detection)
    member.rank_grade = form.get("rank_grade", member.rank_grade)
    member.status = form.get("status", member.status)
    # Team: only touch it if the dropdown was actually submitted.
    _team_present = form.get("team", _MISSING) is not _MISSING
    if _team_present:
        member.team = form.get("team", "").strip() or None

    # Manual team override lock. The team is locked (protected from geo
    # auto-reassignment) if EITHER the admin ticked the "Lock team" checkbox,
    # OR they changed the team dropdown to a different value than was stored
    # (a deliberate manual override). Unchecking the box while leaving the team
    # unchanged clears the lock and hands control back to geo.
    _tl_vals = form.getlist("team_locked")
    checkbox_locked = any(v in ("1", "true", "on", "yes") for v in _tl_vals)
    team_changed = member.team != prev_team
    member.team_locked = checkbox_locked or team_changed
    if form.get("leadership_title", _MISSING) is not _MISSING:
        member.leadership_title = form.get("leadership_title", "").strip() or None
    # Billets: the checkbox group only appears in the POST when at least one is
    # ticked, so an empty list is ambiguous. Only rewrite billets when the form
    # actually rendered the billet section (detected via the required identity
    # gate above, plus a hidden marker). To stay safe, treat a submission that
    # contains ANY assignment-section control as authoritative for billets.
    if _team_present or form.get("leadership_title", _MISSING) is not _MISSING:
        selected_billets = form.getlist("billets")
        member.primary_billet = ", ".join(selected_billets) if selected_billets else None

    # Service record — only overwrite dates when their inputs were submitted.
    if form.get("join_date", _MISSING) is not _MISSING:
        member.join_date = _parse_date(form.get("join_date"))
    if form.get("patch_date", _MISSING) is not _MISSING:
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
    # Checkboxes only submit when checked; the full-form guard above guarantees
    # the whole form rendered, so an absent box legitimately means "unchecked".
    member.is_founder = form.get("is_founder") == "on"
    member.is_veteran = form.get("is_veteran") == "on"
    if form.get("mos", _MISSING) is not _MISSING:
        member.mos = form.get("mos", "").strip() or None

    # Contact — present-only updates (missing input leaves the value alone).
    member.phone = _str_field(form, "phone", member.phone)
    member.address = _str_field(form, "address", member.address)
    member.city = _str_field(form, "city", member.city)
    if form.get("state", _MISSING) is not _MISSING:
        member.state = (form.get("state", "").strip() or "TX")
    member.zip_code = _str_field(form, "zip_code", member.zip_code)
    member.personal_email = _str_field(form, "personal_email", member.personal_email)
    member.emergency_contact = _str_field(form, "emergency_contact", member.emergency_contact)
    member.emergency_phone = _str_field(form, "emergency_phone", member.emergency_phone)

    # --- Auto-parse a full one-line address into city/state/zip ---
    from app.geo import split_oneline_into_fields
    _split = split_oneline_into_fields(
        member.address, member.city, member.state, member.zip_code
    )
    if _split:
        member.address = _split["address"]
        member.city = _split["city"]
        member.state = _split["state"]
        member.zip_code = _split["zip_code"]

    # Radio — present-only updates.
    member.ham_callsign = _str_field(form, "ham_callsign", member.ham_callsign)
    if form.get("ham_license_class", _MISSING) is not _MISSING:
        member.ham_license_class = form.get("ham_license_class", "").strip() or None
    member.gmrs_callsign = _str_field(form, "gmrs_callsign", member.gmrs_callsign)

    # --- Geo team auto-recalculation on address change ---
    from app.geo import geocode_member_fields

    old_team = member.team  # current team (post-assignment) for geo-change compare
    if member.address or member.zip_code:
        try:
            lat, lon = geocode_member_fields(
                member.address, member.city, member.state, member.zip_code
            )
            if lat is not None:
                member.latitude = lat
                member.longitude = lon
                from app.services import teams as _teams
                geo_team, bearing = assign_zone(lat, lon, await _teams.geo_zone_teams())
                if member.team_locked:
                    log.info(f"Geo: {member.first_name} {member.last_name} team LOCKED to "
                             f"{member.team} — coords updated, geo suggests {geo_team} (bearing {bearing:.1f}°) but not applied")
                elif geo_team != old_team:
                    log.info(f"Geo-reassigned {member.first_name} {member.last_name}: "
                             f"{old_team} → {geo_team} (bearing {bearing:.1f}°)")
                    member.team = geo_team
            else:
                log.warning(f"Geocode returned no match for {member.last_name} "
                            f"(address={member.address!r} zip={member.zip_code!r})")
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
        await _sync_leadership_groups(member.nc_username, member.leadership_title, member.team, member.primary_billet)
        await _sync_shop_groups(member.nc_username, old_billets, member.primary_billet)

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
    """Return (subject, html_body, text_body) for the credentials email."""
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
    return subject, html_body, text_body


@router.post("/{member_id}/resend-credentials")
@require_auth
async def resend_credentials(request: Request, member_id: int, db: AsyncSession = Depends(get_db)):
    """Reset the member's NC password and email fresh credentials. S1/Command/Admin."""
    import secrets, string

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

    # 2. Send the credentials email via the shared email service (Proton Bridge)
    from app.integrations import email as email_service
    subject, html_body, text_body = _build_credentials_email(
        member.first_name, member.nc_username, temp_password, member.email)
    if email_service.send_email(member.email, subject, html_body, text=text_body):
        log.info(f"Resent credentials to {member.email} for {member.nc_username} by {user.get('username')}")
    else:
        raise HTTPException(status_code=502, detail="Password was reset but email failed to send")

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/api/members/{member_id}/edit?creds_sent=1", status_code=303)
