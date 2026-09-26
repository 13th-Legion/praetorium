"""Public feedback page. The form itself lives in Nextcloud."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.settings import FEEDBACK_FORM_URL

router = APIRouter(tags=["feedback"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(request: Request):
    user = request.session.get("user")
    return templates.TemplateResponse("pages/feedback.html", {
        "request": request,
        "user": user,
        "form_url": FEEDBACK_FORM_URL,
    })
