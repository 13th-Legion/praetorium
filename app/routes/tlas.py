"""TLAS (Threat Level Assignment System) API routes."""

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.models.tlas import ThreatLevelEntry, ThreatLevel, TLAS_CONFIG

router = APIRouter(prefix="/api/tlas", tags=["tlas"])
templates = Jinja2Templates(directory="app/templates")

from app.constants import COMMAND_ROLES


def is_command(user: dict) -> bool:
    """Check if user belongs to a Command role."""
    roles = set(user.get("roles", []))
    return bool(roles & COMMAND_ROLES)


@router.get("/current")
async def get_current_level(request: Request, db: AsyncSession = Depends(get_db)):
    """Return current threat level + config as HTMX partial or JSON."""
    result = await db.execute(
        select(ThreatLevelEntry).order_by(desc(ThreatLevelEntry.set_at)).limit(1)
    )
    entry = result.scalar_one_or_none()

    if entry:
        level = ThreatLevel(entry.level)
        config = TLAS_CONFIG[level]
        data = {
            "level": level.value,
            "label": config["label"],
            "risk": config["risk"],
            "color_hex": config["color_hex"],
            "color_bg": config["color_bg"],
            "supply_kit": config["supply_kit"],
            "checkin": config["checkin"],
            "measures": config["measures"],
            "set_by": entry.set_by,
            "set_at": entry.set_at.strftime("%Y-%m-%d %H:%M"),
            "note": entry.note,
        }
    else:
        # Default to GREEN
        config = TLAS_CONFIG[ThreatLevel.GREEN]
        data = {
            "level": "green",
            "label": config["label"],
            "risk": config["risk"],
            "color_hex": config["color_hex"],
            "color_bg": config["color_bg"],
            "supply_kit": config["supply_kit"],
            "checkin": config["checkin"],
            "measures": config["measures"],
            "set_by": "System",
            "set_at": "Default",
            "note": None,
        }

    # Return HTMX partial
    return templates.TemplateResponse("partials/tlas_banner.html", {
        "request": request,
        "tlas": data,
    })


@router.post("/set")
@require_auth
async def set_threat_level(request: Request, db: AsyncSession = Depends(get_db)):
    """Set a new threat level. Command group only."""
    user = request.session.get("user", {})
    if not is_command(user):
        raise HTTPException(status_code=403, detail="Only Command can set threat level")

    form = await request.form()
    level_str = form.get("level", "").lower()
    note = form.get("note", "").strip() or None

    # Validate level
    try:
        level = ThreatLevel(level_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid threat level: {level_str}")

    entry = ThreatLevelEntry(
        level=level.value,
        set_by=user.get("display_name", user.get("uid", "unknown")),
        note=note,
    )
    db.add(entry)
    await db.commit()

    from app.routes.notifications import create_notification_for_all
    config = TLAS_CONFIG[level]
    await create_notification_for_all(
        db, "tlas",
        f"⚠️ Threat Level changed to {config['label'].upper()}",
        body=note or f"TLAS is now {level.value.upper()} — {config['risk']}",
        link="/dashboard",
        icon="⚠️"
    )

    # Return updated banner partial (HTMX swap)
    config = TLAS_CONFIG[level]
    data = {
        "level": level.value,
        "label": config["label"],
        "risk": config["risk"],
        "color_hex": config["color_hex"],
        "color_bg": config["color_bg"],
        "supply_kit": config["supply_kit"],
        "checkin": config["checkin"],
        "measures": config["measures"],
        "set_by": entry.set_by,
        "set_at": entry.set_at.strftime("%Y-%m-%d %H:%M"),
        "note": entry.note,
    }
    return templates.TemplateResponse("partials/tlas_banner.html", {
        "request": request,
        "tlas": data,
    })


@router.get("/history")
@require_auth
async def threat_level_history(request: Request, db: AsyncSession = Depends(get_db)):
    """Return recent threat level changes."""
    result = await db.execute(
        select(ThreatLevelEntry).order_by(desc(ThreatLevelEntry.set_at)).limit(20)
    )
    entries = result.scalars().all()

    history = []
    for e in entries:
        lvl = ThreatLevel(e.level)
        cfg = TLAS_CONFIG[lvl]
        history.append({
            "level": lvl.value,
            "label": cfg["label"],
            "color_hex": cfg["color_hex"],
            "set_by": e.set_by,
            "set_at": e.set_at.strftime("%Y-%m-%d %H:%M"),
            "note": e.note,
        })

    return templates.TemplateResponse("partials/tlas_history.html", {
        "request": request,
        "history": history,
    })


@router.get("/admin")
@require_auth
async def tlas_admin_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Admin page for setting threat level. Command only."""
    user = request.session.get("user", {})
    if not is_command(user):
        raise HTTPException(status_code=403, detail="Only Command can access TLAS admin")

    levels = [
        {"value": lvl.value, "label": TLAS_CONFIG[lvl]["label"], "color": TLAS_CONFIG[lvl]["color_hex"]}
        for lvl in ThreatLevel
    ]

    return templates.TemplateResponse("pages/tlas_admin.html", {
        "request": request,
        "user": user,
        "levels": levels,
    })
