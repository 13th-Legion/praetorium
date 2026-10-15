"""S1 admin — TSM unit application routing (unit -> destination mapping).

Maps a state-application-form unit option to either "deck" (13th Legion
pipeline) or an email address (forwarded to that unit's S1). Replaces the
hardcoded COMPANY_ROUTING dict that used to live in the recruit daemon.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_role
from app.database import get_db
from app.models.company_routing import CompanyRouting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/s1/company-routing", tags=["s1-company-routing"])
templates = Jinja2Templates(directory="app/templates")


def _parse_form(form, rid: int | None = None) -> tuple[str, str, str | None]:
    """Validate and return (unit_name, route_type, email) from a form."""
    unit_name = (form.get("unit_name") or "").strip()
    route_type = (form.get("route_type") or "email").strip()
    email = (form.get("email") or "").strip() if route_type == "email" else None

    if not unit_name:
        raise HTTPException(status_code=400, detail="Unit name is required.")
    if route_type not in ("deck", "email"):
        route_type = "email"
    if route_type == "email" and not email:
        raise HTTPException(status_code=400, detail="Email is required for an email route.")
    return unit_name, route_type, email


async def _find_dup(db: AsyncSession, unit_name: str, exclude_id: int | None = None):
    q = select(CompanyRouting).where(CompanyRouting.unit_name == unit_name)
    if exclude_id is not None:
        q = q.where(CompanyRouting.id != exclude_id)
    return (await db.execute(q)).scalar_one_or_none()


@router.get("", response_class=HTMLResponse)
@require_role("s1", "command", "admin")
async def company_routing_page(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(CompanyRouting).order_by(CompanyRouting.unit_name))
    ).scalars().all()
    return templates.TemplateResponse(
        "pages/company_routing.html",
        {"request": request, "user": get_current_user(request), "rows": rows},
    )


@router.post("", response_class=HTMLResponse)
@require_role("s1", "command", "admin")
async def company_routing_create(request: Request, db: AsyncSession = Depends(get_db)):
    unit_name, route_type, email = _parse_form(await request.form())
    if await _find_dup(db, unit_name) is not None:
        raise HTTPException(status_code=400, detail=f"Unit '{unit_name}' already exists.")
    db.add(CompanyRouting(unit_name=unit_name, route_type=route_type, email=email))
    await db.commit()
    return RedirectResponse("/api/s1/company-routing", status_code=303)


@router.post("/{rid}/update", response_class=HTMLResponse)
@require_role("s1", "command", "admin")
async def company_routing_update(rid: int, request: Request, db: AsyncSession = Depends(get_db)):
    row = await db.get(CompanyRouting, rid)
    if row is None:
        raise HTTPException(status_code=404, detail="Routing entry not found.")
    unit_name, route_type, email = _parse_form(await request.form())
    if await _find_dup(db, unit_name, exclude_id=rid) is not None:
        raise HTTPException(status_code=400, detail=f"Unit '{unit_name}' already exists.")
    row.unit_name = unit_name
    row.route_type = route_type
    row.email = email
    await db.commit()
    return RedirectResponse("/api/s1/company-routing", status_code=303)


@router.post("/{rid}/delete", response_class=HTMLResponse)
@require_role("s1", "command", "admin")
async def company_routing_delete(rid: int, request: Request, db: AsyncSession = Depends(get_db)):
    row = await db.get(CompanyRouting, rid)
    if row is not None:
        await db.delete(row)
        await db.commit()
    return RedirectResponse("/api/s1/company-routing", status_code=303)
