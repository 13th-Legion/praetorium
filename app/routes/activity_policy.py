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
    ("no_contact_outreach", "No-contact → first outreach"),
    ("no_contact_response", "No-contact → removal (on top of outreach)"),
    ("no_ftx_verbal", "No-FTX → verbal warning"),
    ("no_ftx_written", "No-FTX → written warning"),
    ("no_ftx_removal", "No-FTX → removal"),
    ("loa", "Leave of Absence length"),
]
UNITS = ["days", "weeks", "months"]


async def _upsert_setting(db: AsyncSession, key: str, value: str, value_type: str) -> None:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row is None:
        db.add(AppSetting(key=key, value=value, value_type=value_type, category="activity"))
    else:
        row.value = value
        row.value_type = value_type
        row.updated_at = datetime.utcnow()


@router.get("", response_class=HTMLResponse)
@require_role("s1", "command", "admin")
async def activity_policy_page(request: Request):
    vals = settings_store.activity_policy()
    return templates.TemplateResponse(
        "pages/s1_activity_policy.html",
        {"request": request, "user": get_current_user(request), "fields": FIELDS, "units": UNITS, "vals": vals},
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
        unit = (form.get(key + "_unit") or current.get(key + "_unit", "weeks")).strip()
        if unit not in UNITS:
            unit = current.get(key + "_unit", "weeks")
        await _upsert_setting(db, "activity_" + key, str(value), "int")
        await _upsert_setting(db, "activity_" + key + "_unit", unit, "str")
    await db.commit()
    settings_store.invalidate()
    return RedirectResponse("/api/s1/activity-policy", status_code=303)
