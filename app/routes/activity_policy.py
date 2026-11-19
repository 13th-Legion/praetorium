"""S1 — Activity Policy configuration (unit policy dials).

S1/Command edit the numeric thresholds in the Activity Policy (no-contact and
no-FTX escalation windows, leave-of-absence length). The signed policy doc and
the Tesserarius activity-nag bot both read these values.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_role, get_current_user
from app.database import get_db
from app.models.app_setting import AppSetting
from app.services import settings_store

router = APIRouter(prefix="/api/s1/activity-policy", tags=["s1-activity-policy"])
templates = Jinja2Templates(directory="app/templates")

FIELDS = [
    ("no_contact_outreach_weeks", "No-contact → first outreach (weeks)"),
    ("no_contact_response_weeks", "No-contact → removal after (weeks, on top of outreach)"),
    ("no_ftx_verbal_months", "No-FTX → verbal warning (months)"),
    ("no_ftx_written_months", "No-FTX → written warning (months)"),
    ("no_ftx_removal_months", "No-FTX → removal (months)"),
    ("loa_months", "Leave of Absence length (months)"),
]


@router.get("", response_class=HTMLResponse)
@require_role("s1", "command", "admin")
async def activity_policy_page(request: Request):
    vals = settings_store.activity_policy()
    return templates.TemplateResponse(
        "pages/s1_activity_policy.html",
        {"request": request, "user": get_current_user(request), "fields": FIELDS, "vals": vals},
    )


@router.post("", response_class=HTMLResponse)
@require_role("s1", "command", "admin")
async def activity_policy_save(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    current = settings_store.activity_policy()
    now = datetime.utcnow()

    for key, _label in FIELDS:
        raw = (form.get(key) or "").strip()
        try:
            value = int(raw)
        except ValueError:
            value = current.get(key, 0)
        db_key = "activity_" + key
        row = (
            await db.execute(select(AppSetting).where(AppSetting.key == db_key))
        ).scalar_one_or_none()
        if row is None:
            db.add(AppSetting(key=db_key, value=str(value), value_type="int", category="activity"))
        else:
            row.value = str(value)
            row.value_type = "int"
            row.updated_at = now
    await db.commit()
    settings_store.invalidate()
    return RedirectResponse("/api/s1/activity-policy", status_code=303)
