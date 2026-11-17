"""User settings and security."""

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member

router = APIRouter(prefix="/settings", tags=["settings"])
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


@router.get("")
@require_auth
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    """User settings page."""
    user = get_current_user(request)
    member = await _get_own_member(request, db)
    return templates.TemplateResponse("pages/settings.html", {
        "request": request,
        "user": user,
        "member": member,
    })


@router.post("/no-media")
@require_auth
async def toggle_no_media(request: Request, db: AsyncSession = Depends(get_db)):
    """Self-service opt-in/out of photos, video, and social media."""
    member = await _get_own_member(request, db)
    form = await request.form()
    member.no_media = form.get("no_media") == "on"
    await db.commit()
    state = "opted OUT of" if member.no_media else "opted back IN to"
    return HTMLResponse(
        f"<span style='color:#00C851;'>✅ Updated — you are now {state} photos, video, and social media.</span>"
    )
