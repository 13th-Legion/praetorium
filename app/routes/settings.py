"""User settings and security."""
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from app.auth import require_auth, get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/templates")

@router.get("")
@require_auth
async def settings_page(request: Request):
    """User settings page."""
    user = get_current_user(request)
    return templates.TemplateResponse("pages/settings.html", {
        "request": request,
        "user": user,
    })
