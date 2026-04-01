"""Authentication helpers — NC OAuth2 + session management."""

from functools import wraps
from typing import Optional

from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
import httpx

from config import get_settings

settings = get_settings()


def get_current_user(request: Request) -> Optional[dict]:
    """Get user dict from session, or None."""
    return request.session.get("user")


def require_auth(func):
    """Decorator: redirect to login if not authenticated."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        user = get_current_user(request)
        if not user:
            return RedirectResponse(url="/auth/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper


def require_role(*roles: str):
    """Decorator: require one of the specified portal roles."""
    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            user = get_current_user(request)
            if not user:
                return RedirectResponse(url="/auth/login", status_code=302)
            user_roles = set(user.get("roles", []))
            if not user_roles.intersection(set(roles)):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


async def fetch_nc_groups(username: str) -> list[str]:
    """Fetch a user's NC groups via the provisioning API."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{settings.nc_url}/ocs/v2.php/cloud/users/{username}",
            auth=(settings.nc_api_user, settings.nc_api_password),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("ocs", {}).get("data", {}).get("groups", [])


# NC Group → Portal Role mapping
GROUP_ROLE_MAP = {
    "admin": "admin",
    "Command": "command",
    "Leaders": "leader",
    "Rank - Officer": "officer",
    "Rank - NCO": "nco",
    "Rank - Enlisted": "enlisted",
    "Rank - Recruit": "recruit",
    "[S-1] Admin": "s1",
    "[S-2] Intel & Security": "s2",
    "[S-3] Training & Ops": "s3",
    "[S-4] Logistics": "s4",
    "[S-5] Medical": "s5",
    "[S-6] Comms": "s6",
    "Team - Headquarters": "team_hq",
    "Team - Arrow": "team_arrow",
    "Team - Badger": "team_badger",
    "Team - Chaos": "team_chaos",
    "Team - Delta": "team_delta",
    "Recruiters": "recruiter",
    "[S-1] Lead": "s1_lead",
}


def map_groups_to_roles(nc_groups: list[str]) -> list[str]:
    """Convert NC group names to portal role strings."""
    roles = []
    for group in nc_groups:
        if group in GROUP_ROLE_MAP:
            roles.append(GROUP_ROLE_MAP[group])
    return roles
