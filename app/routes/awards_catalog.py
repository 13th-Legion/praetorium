"""Awards catalog — what each decoration, ribbon, tab, and service disc is worth.

Shown to every signed-in member. The numbers come from ribbon_catalog, so a
point change does not need a second copy of the list.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_auth
from app.database import get_db
from app.models.ribbons import RibbonCatalog

router = APIRouter(tags=["awards-catalog"])
templates = Jinja2Templates(directory="app/templates")

SECTIONS = (
    ("dona", "Decorations", "Dona Militaria", "Highest awards. These sit above the ribbon rack."),
    ("rack", "Ribbon rack", None, "Worn four across, highest first. Another award of the same ribbon adds the device points."),
    ("tab", "Tabs", None, "Qualification tabs."),
    ("tenure", "Years of service", "Anni Stipendiorum", "Counted from the join date. Gold is five years, silver is three, and bronze is one."),
)


_ART_FOLDER = {"dona": "dona", "rack": "rack", "tab": "tabs", "tenure": "discs"}


def image_path(section: str, code: str, image: str | None) -> str:
    """Catalog art path under /static/img/ribbons/."""
    if image:
        return image
    return f"{_ART_FOLDER.get(section, section)}/{code}.png"


@router.get("/awards")
@require_auth
async def awards_catalog(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    rows = (
        await db.execute(
            select(RibbonCatalog)
            .where(RibbonCatalog.active.is_(True))
            .order_by(RibbonCatalog.precedence, RibbonCatalog.name)
        )
    ).scalars().all()

    grouped = {key: [] for key, *_rest in SECTIONS}
    for row in rows:
        if row.section not in grouped:
            continue
        grouped[row.section].append({
            "name": row.name,
            "image": image_path(row.section, row.code, row.image),
            "points": row.base_points,
            "device_increment": row.device_increment or 0,
            "description": row.description,
            "is_auto": row.is_auto,
        })

    sections = [
        {"key": key, "title": title, "latin": latin, "blurb": blurb, "items": grouped[key]}
        for key, title, latin, blurb in SECTIONS
    ]
    return templates.TemplateResponse("pages/awards_catalog.html", {
        "request": request,
        "user": user,
        "sections": sections,
    })
