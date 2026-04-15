"""Shop dashboard routes — RBAC-gated per shop."""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi.templating import Jinja2Templates

from app.auth import require_auth, get_current_user
from app.database import get_db

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/shops", tags=["shops"])

# Shop RBAC: which roles can access which shop
SHOP_ACCESS = {
    "s1": {"s1", "command", "admin"},
    "s2": {"s2", "command", "admin"},
    "s3": {"s3", "command", "admin", "leader"},
    "s4": {"s4", "command", "admin"},
    "s5": {"s5", "command", "admin"},
    "s6": {"s6", "command", "admin"},
}

SHOP_META = {
    "s1": {"name": "S1 — Personnel", "icon": "📋", "has_dashboard": True},
    "s2": {"name": "S2 — Intel & Security", "icon": "🔍", "has_dashboard": False},
    "s3": {"name": "S3 — Ops & Training", "icon": "⚔️", "has_dashboard": True},
    "s4": {"name": "S4 — Logistics", "icon": "📦", "has_dashboard": False},
    "s5": {"name": "S5 — Medical", "icon": "🩹", "has_dashboard": False},
    "s6": {"name": "S6 — Comms", "icon": "📡", "has_dashboard": False},
}


def _check_shop_access(user: dict, shop: str) -> bool:
    """Check if user has access to the given shop."""
    user_roles = set(user.get("roles", []))
    required = SHOP_ACCESS.get(shop, set())
    return bool(user_roles & required)


@router.get("/s1")
@require_auth
async def shop_s1(request: Request):
    """S1 dashboard — redirect to existing pipeline for now."""
    user = get_current_user(request)
    if not _check_shop_access(user, "s1"):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)
    return RedirectResponse("/api/s1/pipeline", status_code=302)


@router.get("/s3")
@require_auth
async def shop_s3(request: Request, db: AsyncSession = Depends(get_db)):
    """S3 Ops & Training dashboard."""
    user = get_current_user(request)
    if not _check_shop_access(user, "s3"):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)
    return templates.TemplateResponse("pages/shop_s3.html", {
        "request": request,
        "user": user,
    })


@router.get("/{shop}")
@require_auth
async def shop_placeholder(request: Request, shop: str):
    """Placeholder for shops not yet built."""
    user = get_current_user(request)
    if shop not in SHOP_META:
        return HTMLResponse("<h2>Not Found</h2>", status_code=404)
    if not _check_shop_access(user, shop):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)
    meta = SHOP_META[shop]
    return templates.TemplateResponse("pages/shop_placeholder.html", {
        "request": request,
        "user": user,
        "shop": shop,
        "shop_name": meta["name"],
        "shop_icon": meta["icon"],
    })
