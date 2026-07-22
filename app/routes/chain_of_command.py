"""Chain of Command — live org chart + reporting-map admin (PP-243).

The chart renders from live roster data:
  - shop membership/leads are parsed from members.primary_billet
  - fireteams from members.team + leadership_title
  - command tier from leadership_title + is_hq
The shop->command reporting map is stored in shop_reporting (editable by Command).
The battalion/TSM growth doctrine text is static prose in the template.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth, require_role, get_current_user
from app.database import get_db, async_session
from app.models.member import Member
from app.models.org import ShopReporting
from app.constants import RANK_ABBR

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chain-of-command", tags=["chain-of-command"])

CONFIG_ROLES = ("command", "admin")

# Rank grade -> army.mil insignia asset (served from /static/img/ranks/).
# E-1 (recruit) has no insignia.
RANK_INSIGNIA = {
    "E-2": "enl_private.svg",
    "E-3": "enl_private_first_class.svg",
    "E-4": "enl_corporal.svg",
    "E-5": "enl_sergeant.svg",
    "E-6": "enl_staff_sergeant.svg",
    "E-7": "enl_sergeant_first_class.svg",
    "E-8": "enl_first_sergeant.svg",
    "E-8M": "enl_master_sergeant.svg",
    "E-9": "enl_sergeant_major.svg",
    "W-2": "wo_cw2.svg",
    "W-3": "wo_cw3.svg",
    "O-1": "off_second_lieutenant.svg",
    "O-2": "off_first_lieutenant.svg",
    "O-3": "off_captain.svg",
    "O-4": "off_major.svg",
}

RANK_ORDER = {
    "E-1": 1, "E-2": 2, "E-3": 3, "E-4": 4, "E-5": 5,
    "E-6": 6, "E-7": 7, "E-8": 8, "E-8M": 8, "E-9": 9,
    "W-1": 10, "W-2": 11, "W-3": 12,
    "O-1": 13, "O-2": 14, "O-3": 15, "O-4": 16,
}

SHOP_DEFS = [
    ("S1", "S1 — Administration", "📋"),
    ("S2", "S2 — Intelligence & Security", "🔍"),
    ("S3", "S3 — Operations & Training", "⚔️"),
    ("S4", "S4 — Logistics", "📦"),
    ("S5", "S5 — Medical", "🏥"),
    ("S6", "S6 — Communications", "📡"),
]

TEAM_DEFS = [
    ("Aquila", "North (330°–30°)"),
    ("Bravo", "Northeast (30°–90°)"),
    ("Charlie", "East/SE (90°–150°)"),
    ("Delta", "South (150°–210°)"),
    ("Echo", "SW/West (210°–270°)"),
    ("Foxtrot", "Northwest (270°–330°)"),
]

# Rank names templates use for the mini insignia; recruits (E-1) fall back to text.
ACTIVE_STATUSES = ("active", "recruit")


def _mini(m: Member) -> dict:
    """Lightweight member dict for the template."""
    return {
        "id": m.id,
        "first": m.first_name,
        "last": m.last_name,
        "callsign": m.callsign,
        "rank_grade": m.rank_grade,
        "rank_abbr": RANK_ABBR.get(m.rank_grade or "", ""),
        "insignia": RANK_INSIGNIA.get(m.rank_grade or ""),
        "is_recruit": (m.status == "recruit") or (m.rank_grade in (None, "", "E-1")),
        "leadership_title": m.leadership_title,
    }


def _individual_billets(m: Member) -> list[str]:
    return [b.strip() for b in (m.primary_billet or "").split(",") if b.strip()]


async def _build_org(db: AsyncSession) -> dict:
    """Assemble the full org structure from live roster + reporting map."""
    res = await db.execute(
        select(Member).where(Member.status.in_(ACTIVE_STATUSES))
    )
    members = list(res.scalars().all())
    by_id = {m.id: m for m in members}

    # ── Command tier (HQ leadership by title) ──
    def find_title(*titles):
        for m in members:
            if (m.leadership_title or "") in titles:
                return m
        return None

    command = {
        "co": find_title("Commanding Officer"),
        "xo": find_title("Executive Officer"),
        "first_sergeant": find_title("First Sergeant"),
        "platoon_leader": find_title("Platoon Leader"),
        "platoon_sergeant": find_title("Platoon Sergeant"),
    }
    command_view = {k: (_mini(v) if v else None) for k, v in command.items()}

    # ── Reporting map ──
    rres = await db.execute(select(ShopReporting))
    reporting = {r.shop_key: r for r in rres.scalars().all()}

    # ── Shops (parsed live from billets) ──
    shops = []
    for prefix, name, emoji in SHOP_DEFS:
        lead = None
        shop_members = []
        for m in members:
            billets = _individual_billets(m)
            in_shop = any(b.startswith(prefix + ":") for b in billets)
            if not in_shop:
                continue
            is_lead = any(b.startswith(prefix + ":") and "(Lead)" in b for b in billets)
            if is_lead and lead is None:
                lead = m
            else:
                shop_members.append(m)
        if lead and lead in shop_members:
            shop_members.remove(lead)
        shop_members.sort(key=lambda m: -RANK_ORDER.get(m.rank_grade or "E-1", 0))

        rep = reporting.get(prefix)
        reports_to = None
        command_led = False
        note = None
        if rep:
            command_led = rep.command_led
            note = rep.note
            if rep.reports_to_member_id and rep.reports_to_member_id in by_id:
                reports_to = _mini(by_id[rep.reports_to_member_id])

        # sub-billet label (e.g. "(Recruiting)") per member for display
        member_views = []
        for m in shop_members:
            sub = None
            for b in _individual_billets(m):
                if b.startswith(prefix + ":"):
                    detail = b.split(":", 1)[1].strip()
                    # keep only the qualifier after the shop name if present
                    sub = detail
                    break
            mv = _mini(m)
            mv["shop_detail"] = sub
            member_views.append(mv)

        shops.append({
            "prefix": prefix,
            "name": name,
            "emoji": emoji,
            "lead": _mini(lead) if lead else None,
            "members": member_views,
            "reports_to": reports_to,
            "command_led": command_led,
            "note": note,
        })

    # ── Fireteams (geographic) ──
    # DB-backed team list (single source of truth) so renames persist. Zone
    # direction labels are positional by geo_zone_index.
    from app.services import teams as _teams
    _zone_labels = [
        "North (330°–30°)", "Northeast (30°–90°)", "East/SE (90°–150°)",
        "South (150°–210°)", "SW/West (210°–270°)", "Northwest (270°–330°)",
    ]
    _geo = [t for t in await _teams.all_teams() if t.geo_zone_index is not None]
    _geo.sort(key=lambda t: t.geo_zone_index)
    _team_defs = [
        (t.name, _zone_labels[t.geo_zone_index] if t.geo_zone_index < len(_zone_labels) else "")
        for t in _geo
    ] or TEAM_DEFS
    teams = []
    for team_key, zone in _team_defs:
        tmembers = [m for m in members if (m.team or "") == team_key]
        # HQ-element members keep their geo team as a zone assignment but must not
        # hold a geo fireteam TL/ATL slot (their leadership_title reflects the HQ
        # element, not this fireteam). Exclude them from TL/ATL selection only;
        # they still render as rank-and-file in the team.
        tl = next((m for m in tmembers if (m.leadership_title or "") == "Team Leader" and not getattr(m, "is_hq", False)), None)
        atl = next((m for m in tmembers if (m.leadership_title or "") == "Assistant Team Leader" and not getattr(m, "is_hq", False)), None)
        rank_file = [m for m in tmembers if m not in (tl, atl)]
        rank_file.sort(key=lambda m: -RANK_ORDER.get(m.rank_grade or "E-1", 0))
        teams.append({
            "key": team_key,
            "zone": zone,
            "tl": _mini(tl) if tl else None,
            "atl": _mini(atl) if atl else None,
            "members": [_mini(m) for m in rank_file],
            "count": len(tmembers),
        })

    return {"command": command_view, "shops": shops, "teams": teams}


@router.get("")
@require_auth
async def chain_of_command(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    org = await _build_org(db)
    can_edit = bool(set(user.get("roles", [])) & set(CONFIG_ROLES))
    # Import templates lazily to avoid circular import at module load.
    from app.routes.roster import templates
    return templates.TemplateResponse("pages/chain_of_command.html", {
        "request": request,
        "user": user,
        "org": org,
        "can_edit": can_edit,
        "now": datetime.utcnow(),
    })


@router.get("/config")
@require_role(*CONFIG_ROLES)
async def config_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    # Command members available as "reports to" targets.
    res = await db.execute(select(Member).where(Member.status.in_(ACTIVE_STATUSES)))
    members = list(res.scalars().all())
    command_choices = [
        m for m in members
        if (m.leadership_title or "") in (
            "Commanding Officer", "Executive Officer",
            "First Sergeant", "Platoon Leader", "Platoon Sergeant",
        )
    ]
    command_choices.sort(key=lambda m: -RANK_ORDER.get(m.rank_grade or "E-1", 0))

    rres = await db.execute(select(ShopReporting))
    reporting = {r.shop_key: r for r in rres.scalars().all()}

    rows = []
    for prefix, name, emoji in SHOP_DEFS:
        rep = reporting.get(prefix)
        rows.append({
            "prefix": prefix,
            "name": name,
            "emoji": emoji,
            "reports_to_id": rep.reports_to_member_id if rep else None,
            "command_led": rep.command_led if rep else False,
            "note": rep.note if rep else "",
        })

    from app.routes.roster import templates
    return templates.TemplateResponse("pages/chain_of_command_config.html", {
        "request": request,
        "user": user,
        "rows": rows,
        "command_choices": [
            {"id": m.id, "label": f"{RANK_ABBR.get(m.rank_grade or '', '')} {m.last_name}"
             + (f' "{m.callsign}"' if m.callsign else "")
             + (f" — {m.leadership_title}" if m.leadership_title else "")}
            for m in command_choices
        ],
    })


@router.post("/config")
@require_role(*CONFIG_ROLES)
async def config_save(request: Request):
    form = await request.form()
    async with async_session() as db:
        rres = await db.execute(select(ShopReporting))
        reporting = {r.shop_key: r for r in rres.scalars().all()}

        for prefix, _name, _emoji in SHOP_DEFS:
            command_led = form.get(f"{prefix}_command_led") == "on"
            raw_rid = (form.get(f"{prefix}_reports_to") or "").strip()
            note = (form.get(f"{prefix}_note") or "").strip() or None
            rid = int(raw_rid) if raw_rid.isdigit() else None
            if command_led:
                rid = None  # command-led shops have no separate report target

            rep = reporting.get(prefix)
            if rep is None:
                rep = ShopReporting(shop_key=prefix)
                db.add(rep)
            rep.reports_to_member_id = rid
            rep.command_led = command_led
            rep.note = note
        await db.commit()
    return RedirectResponse(url="/chain-of-command/config?saved=1", status_code=302)
