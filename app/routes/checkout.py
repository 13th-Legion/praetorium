"""Public checkout page for $50 Application Fee."""

import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")


@router.get("/apply/fee", response_class=HTMLResponse)
async def application_fee_checkout(request: Request, email: str = ""):
    """Public page for recruits to pay the $50 application fee."""
    return templates.TemplateResponse("pages/checkout.html", {
        "request": request,
        "email": email,
        "paypal_client_id": PAYPAL_CLIENT_ID,
    })