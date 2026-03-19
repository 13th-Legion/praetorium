"""Debug route — admin only."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import get_current_user

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/session")
async def session_info(request: Request):
    """Dump current session data (admin only)."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if "admin" not in set(user.get("roles", [])):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse(user)
