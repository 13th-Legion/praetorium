"""Dashboard route — authenticated landing page."""

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.auth import require_auth

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")

from app.constants import COMMAND_ROLES


@router.get("/dashboard")
@require_auth
async def dashboard(request: Request):
    user = request.session.get("user", {})
    roles = set(user.get("roles", []))
    return templates.TemplateResponse("pages/dashboard.html", {
        "request": request,
        "user": user,
        "is_command": bool(roles & COMMAND_ROLES),
        "is_s1_lead": "s1" in roles,
    })
