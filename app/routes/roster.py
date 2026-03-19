"""Roster directory — PP-023. RBAC-filtered views."""

from datetime import date, datetime

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member
from config import get_settings

router = APIRouter(prefix="/roster", tags=["roster"])
templates = Jinja2Templates(directory="app/templates")

from zoneinfo import ZoneInfo
_tz_central = ZoneInfo("America/Chicago")
_tz_utc = ZoneInfo("UTC")

def _timestamp_fmt(epoch_secs):
    try:
        utc_dt = datetime.fromtimestamp(int(epoch_secs), tz=_tz_utc)
        local_dt = utc_dt.astimezone(_tz_central)
        tz_abbr = local_dt.strftime("%Z")  # CST or CDT
        return local_dt.strftime(f"%d %b %Y %H%M {tz_abbr}")
    except Exception:
        return "Unknown"
templates.env.filters["timestamp_fmt"] = _timestamp_fmt

RANK_ORDER = {
    "E-1": 1, "E-2": 2, "E-3": 3, "E-4": 4, "E-5": 5,
    "E-6": 6, "E-7": 7, "E-8": 8, "E-9": 9,
    "W-1": 10, "W-2": 11,
    "O-1": 12, "O-2": 13, "O-3": 14, "O-4": 15,
}

from app.constants import RANK_ABBR, TEAM_ORDER

# Portal role → NC group name → team name mapping for filtering
ROLE_TO_TEAM = {
    "team_hq": "Headquarters",
    "team_alpha": "Alpha",
    "team_bravo": "Bravo",
    "team_charlie": "Charlie",
    "team_delta": "Delta",
    "team_echo": "Echo",
    "team_foxtrot": "Foxtrot",
}

# S-shop billet substrings to match
ROLE_TO_SHOP = {
    "s1": "S1",
    "s2": "S2",
    "s3": "S3",
    "s4": "S4",
    "s5": "S5",
    "s6": "S6",
}

# Roles that can see the full roster
FULL_ROSTER_ROLES = {"admin", "command", "leader", "officer", "nco", "s1"}

# Cache NC usernames (refreshed each request for now — could cache w/ TTL later)
import time as _time
_nc_cache: dict = {"data": {}, "ts": 0}
_NC_CACHE_TTL = 300  # 5 minutes

async def _fetch_nc_users() -> dict:
    if _time.time() - _nc_cache["ts"] < _NC_CACHE_TTL and _nc_cache["data"]:
        return _nc_cache["data"]
    """Fetch all NC usernames + last login via the provisioning API.
    
    Returns dict of {username: last_login_epoch_ms} for accounts that exist.
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient() as client:
            # Get user list
            r = await client.get(
                f"{settings.nc_url}/ocs/v2.php/cloud/users",
                auth=(settings.nc_api_user, settings.nc_api_password),
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json().get("ocs", {}).get("data", {})
            users = data.get("users", []) if isinstance(data, dict) else data

            # Fetch last login for each user (batch)
            result = {}
            for username in users:
                try:
                    ur = await client.get(
                        f"{settings.nc_url}/ocs/v2.php/cloud/users/{username}",
                        auth=(settings.nc_api_user, settings.nc_api_password),
                        headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                        timeout=5,
                    )
                    ur.raise_for_status()
                    udata = ur.json().get("ocs", {}).get("data", {})
                    result[username] = udata.get("lastLogin", 0)
                except Exception:
                    result[username] = 0
            _nc_cache["data"] = result
            _nc_cache["ts"] = _time.time()
            return result
    except Exception:
        return {}


def _get_user_team(roles: set) -> str | None:
    """Extract the user's team from their portal roles."""
    for role, team in ROLE_TO_TEAM.items():
        if role in roles:
            return team
    return None


def _get_user_shops(roles: set) -> list[str]:
    """Extract shop prefixes the user belongs to."""
    shops = []
    for role, prefix in ROLE_TO_SHOP.items():
        if role in roles:
            shops.append(prefix)
    return shops


@router.get("")
@require_auth
async def roster_list(request: Request, db: AsyncSession = Depends(get_db)):
    """Roster view — full for leadership, team+shop scoped for everyone else."""
    user = get_current_user(request)
    user_roles = set(user.get("roles", []))

    can_see_full = bool(user_roles & FULL_ROSTER_ROLES)
    can_see_pii = bool(user_roles & {"admin", "command", "leader", "officer", "nco", "s1"})

    # Roster tabs: active (default), inactive, blacklist
    show_tab = request.query_params.get("show", "active")
    if show_tab not in ("active", "inactive", "blacklist"):
        show_tab = "active"

    # Build query based on tab
    if show_tab == "inactive" and can_see_full:
        query = select(Member).where(Member.status.in_(["inactive", "separated"]))
    elif show_tab == "blacklist" and can_see_full:
        query = select(Member).where(Member.status == "blacklisted")
    else:
        show_tab = "active"
        query = select(Member).where(Member.status.in_(["active", "recruit"]))

    if can_see_full:
        # Leadership sees everything
        view_scope = "full"
        page_title = "Personnel Roster"
    else:
        # Everyone else sees their team + anyone in their shop(s)
        user_team = _get_user_team(user_roles)
        user_shops = _get_user_shops(user_roles)

        filters = []
        scope_parts = []

        if user_team:
            filters.append(Member.team == user_team)
            scope_parts.append(f"{user_team} team")

        for shop_prefix in user_shops:
            filters.append(Member.primary_billet.ilike(f"%{shop_prefix}%"))
            scope_parts.append(f"{shop_prefix} shop")

        # Always include self
        filters.append(Member.nc_username == user["username"])

        if filters:
            query = query.where(or_(*filters))

        view_scope = " and ".join(scope_parts) if scope_parts else "assigned personnel"
        page_title = "My Roster"

    result = await db.execute(query)
    members = result.scalars().all()

    # View mode: "team" (geo teams, default) or "element" (HQ + fireteams org chart)
    view_mode = request.query_params.get("view", "element")
    if view_mode not in ("team", "element"):
        view_mode = "element"

    # Leadership sort priority within each team
    def _leader_priority(title: str, is_element_view: bool = False) -> int:
        t = (title or "").lower()
        if is_element_view:
            # Element view: command staff at top of HQ section
            if "commanding officer" in t: return 0
            if "executive officer" in t: return 1
            if "first sergeant" in t: return 2
            if "platoon sergeant" in t or "training nco" in t: return 3
        # Team view (and element fireteam sections): TL/ATL first, then by rank
        if "team leader" in t and "assistant" not in t: return 10
        if "assistant team leader" in t: return 11
        return 50

    def _sort_key(m, team_name=""):
        title = (m.leadership_title or "").strip()
        rank_val = RANK_ORDER.get(m.rank_grade or "E-1", 0)
        is_element = view_mode == "element" and team_name == "Headquarters"
        prio = _leader_priority(title, is_element_view=is_element)
        return (prio, -rank_val)

    # Group by team and sort
    teams = {}
    for m in members:
        team = m.team or "Unassigned"
        if team not in teams:
            teams[team] = []
        teams[team].append(m)

    # In "element" view, pull HQ members into a separate Headquarters section
    hq_members = []
    if view_mode == "element":
        hq_members = [m for m in members if getattr(m, "is_hq", False)]
        hq_members.sort(key=lambda m: _sort_key(m, "Headquarters"))
        # Remove HQ members from their geo teams so they don't appear twice
        for team_name in list(teams.keys()):
            teams[team_name] = [m for m in teams[team_name] if not getattr(m, "is_hq", False)]
            if not teams[team_name]:
                del teams[team_name]

    sorted_teams = sorted(teams.items(), key=lambda t: TEAM_ORDER.get(t[0], 99))

    for team_name, team_members in sorted_teams:
        team_members.sort(key=lambda m, tn=team_name: _sort_key(m, tn))

    # NC last-login data removed from roster template — skip the expensive API calls
    nc_users = {}
    nc_accounts = set()

    # TIS helper function for template
    def tis(join_date):
        if not join_date:
            return "—"
        today = date.today()
        months = (today.year - join_date.year) * 12 + (today.month - join_date.month)
        years = months // 12
        rem = months % 12
        if years > 0:
            return f"{years}y {rem}m"
        return f"{rem}m"

    # Determine which teams the user can rename (for edit icon)
    renameable_teams = set()
    if can_see_full:
        # Command/S1 can rename any team
        renameable_teams = {t[0] for t in sorted_teams if t[0] != "Headquarters"}
    else:
        # Check if user is a TL — find their member record
        username = user.get("username")
        if username:
            tl_result = await db.execute(
                select(Member.team).where(
                    Member.nc_username == username,
                    Member.leadership_title == "Team Leader",
                )
            )
            tl_row = tl_result.scalar_one_or_none()
            if tl_row:
                renameable_teams = {tl_row}

    return templates.TemplateResponse("pages/roster.html", {
        "request": request,
        "user": user,
        "teams": sorted_teams,
        "hq_members": hq_members,
        "view_mode": view_mode,
        "rank_abbr": RANK_ABBR,
        "can_see_pii": can_see_pii,
        "can_see_full": can_see_full,
        "total": len(members),
        "view_scope": view_scope,
        "page_title": page_title,
        "nc_accounts": nc_accounts,
        "nc_users": nc_users,
        "tis": tis,
        "show_tab": show_tab,
        "renameable_teams": renameable_teams,
    })


@router.get("/map")
@require_auth
async def member_map(request: Request):
    """Geographic team map — Leaders, Command, S1 only."""
    user = get_current_user(request)
    roles = set(user.get("roles", []))
    allowed = {"command", "admin", "s1", "leader"}
    if not (roles & allowed):
        raise HTTPException(status_code=403, detail="Map access restricted to leadership")
    return templates.TemplateResponse("pages/map.html", {"request": request, "user": user})
