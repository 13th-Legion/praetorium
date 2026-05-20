"""Public donation page for the 13th Legion."""

import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")


@router.get("/donate", response_class=HTMLResponse)
async def donate_page(request: Request):
    """Public page for donations to the 13th Legion."""
    user = request.session.get("user") if hasattr(request, "session") else None
    return templates.TemplateResponse("pages/donate.html", {
        "request": request,
        "paypal_client_id": PAYPAL_CLIENT_ID,
        "user_email": (user or {}).get("email", ""),
        "user_name": (user or {}).get("display_name", ""),
    })
