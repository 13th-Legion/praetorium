"""Team Management — TL can rename their team, Command can rename any."""

import logging
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member
from app.constants import (
    TEAM_DESIGNATION, TEAM_TALK_TOKENS, TEAM_ORDER,
    COMMAND_ROLES, S1_ROLES,
)
from config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/team", tags=["team"])
templates = Jinja2Templates(directory="app/templates")


async def _get_logged_in_member(request: Request, db: AsyncSession) -> Member | None:
    user = get_current_user(request)
    username = user.get("username")
    if not username:
        return None
    result = await db.execute(select(Member).where(Member.nc_username == username))
    return result.scalar_one_or_none()


def _can_rename(user: dict, member: Member | None) -> tuple[bool, str | None]:
    """Check if user can rename a team. Returns (allowed, locked_team).
    locked_team=None means they can rename any team (Command/S1).
    """
    roles = set(user.get("roles", []))

    # Command/S1 can rename any team
    if roles & COMMAND_ROLES or roles & S1_ROLES:
        return True, None

    # TL can rename their own team
    if member and member.leadership_title == "Team Leader" and member.team:
        return True, member.team

    return False, None


def _get_designation_letter(team_name: str) -> str | None:
    """Get the designation letter for a team, even if renamed."""
    if team_name in TEAM_DESIGNATION:
        return TEAM_DESIGNATION[team_name]
    # Already renamed — first letter IS the designation
    first = team_name[0].upper() if team_name else None
    if first in TEAM_DESIGNATION.values():
        return first
    return None


@router.get("/rename")
@require_auth
async def rename_team_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Team rename form."""
    user = get_current_user(request)
    member = await _get_logged_in_member(request, db)
    allowed, locked_team = _can_rename(user, member)

    if not allowed:
        raise HTTPException(status_code=403, detail="Only Team Leaders or Command can rename teams")

    roles = set(user.get("roles", []))
    is_command = bool(roles & COMMAND_ROLES) or bool(roles & S1_ROLES)

    # Get current team names
    result = await db.execute(
        select(Member.team).where(
            Member.status.in_(("active", "recruit")),
            Member.team.isnot(None),
            Member.team != "Headquarters",
        ).distinct()
    )
    current_teams = sorted([r[0] for r in result.all()], key=lambda t: TEAM_ORDER.get(t, 99))

    return templates.TemplateResponse("pages/team_rename.html", {
        "request": request,
        "user": user,
        "teams": current_teams,
        "locked_team": locked_team,
        "is_command": is_command,
        "designations": {t: _get_designation_letter(t) for t in current_teams},
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
    })


@router.post("/rename")
@require_auth
async def rename_team_submit(request: Request, db: AsyncSession = Depends(get_db)):
    """Process team rename."""
    user = get_current_user(request)
    member = await _get_logged_in_member(request, db)
    allowed, locked_team = _can_rename(user, member)

    if not allowed:
        raise HTTPException(status_code=403, detail="Only Team Leaders or Command can rename teams")

    form = await request.form()
    old_name = form.get("team", "").strip()
    new_name = form.get("new_name", "").strip()

    if not old_name or not new_name:
        return RedirectResponse(url="/team/rename?error=Team+and+new+name+are+required", status_code=303)

    # If TL, only their own team
    if locked_team and old_name != locked_team:
        raise HTTPException(status_code=403, detail="You can only rename your own team")

    # Validate: must start with correct designation letter
    letter = _get_designation_letter(old_name)
    if letter and new_name[0].upper() != letter:
        return RedirectResponse(
            url=f"/team/rename?error=Name+must+start+with+'{letter}'", status_code=303
        )

    # Capitalize first letter
    new_name = new_name[0].upper() + new_name[1:]

    if old_name == new_name:
        return RedirectResponse(url="/roster", status_code=303)

    by = user.get("display_name", user.get("uid", "unknown"))
    log.info(f"Team rename: {old_name} → {new_name} (by {by})")

    # 1. Update all members in portal DB
    await db.execute(
        update(Member).where(Member.team == old_name).values(team=new_name)
    )
    await db.commit()

    # 2. Update TEAM_ORDER at runtime
    if old_name in TEAM_ORDER:
        order_val = TEAM_ORDER.pop(old_name)
        TEAM_ORDER[new_name] = order_val

    # 3. Rename NC Talk channel
    talk_token = TEAM_TALK_TOKENS.get(old_name)
    if talk_token:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.put(
                    f"{settings.nc_url}/ocs/v2.php/apps/spreed/api/v4/room/{talk_token}",
                    auth=(settings.nc_api_user, settings.nc_api_password),
                    headers={"OCS-APIRequest": "true", "Content-Type": "application/json"},
                    json={"roomName": f"T1 · {new_name}"},
                    timeout=10,
                )
                if r.status_code == 200:
                    log.info(f"Renamed NC Talk room {talk_token} → T1 · {new_name}")
                    TEAM_TALK_TOKENS[new_name] = TEAM_TALK_TOKENS.pop(old_name)
                else:
                    log.warning(f"NC Talk rename failed: {r.status_code}")
        except Exception as e:
            log.error(f"NC Talk rename error: {e}")

    return RedirectResponse(url=f"/team/rename?success={old_name}+→+{new_name}", status_code=303)
