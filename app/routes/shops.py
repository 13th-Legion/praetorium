"""Shop dashboard routes — RBAC-gated per shop."""

import re
from datetime import datetime

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi.templating import Jinja2Templates

from app.auth import require_auth, get_current_user
from app.database import get_db
from app.models.member import Member
from app.models.org import ShopSignupRequest

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/shops", tags=["shops"])

# Max shops a member may sign up to hold. Enforced at the sign-up form + submit
# only; some members are grandfathered into more and that is fine (accept does
# not re-enforce).
MAX_SHOPS = 2

# Shop catalog for the sign-up page (canonical key -> display + description).
SHOP_SIGNUP_CATALOG = [
    {"key": "S1", "name": "S1 — Personnel & Administration", "icon": "📋",
     "desc": "Recruiting pipeline, onboarding, roster and records, promotions, "
             "awards and ribbons, documents, and unit communications. The admin "
             "backbone of the company."},
    {"key": "S2", "name": "S2 — Intelligence & Security", "icon": "🔍",
     "desc": "Area studies, threat analysis, OPSEC, and security. Produces the "
             "intel products that inform planning and keep the unit sharp."},
    {"key": "S3", "name": "S3 — Operations & Training", "icon": "⚔️",
     "desc": "Plans and runs FTXs and training: event building, TRADOC blocks, "
             "weapons qualification, land nav, and attendance tracking. Where the "
             "training schedule comes to life."},
    {"key": "S4", "name": "S4 — Logistics", "icon": "📦",
     "desc": "Supply, equipment, transport, and sustainment. Makes sure the unit "
             "has the gear and support it needs in the field."},
    {"key": "S5", "name": "S5 — Medical", "icon": "🩹",
     "desc": "Combat lifesaver and medical training, aid planning, and health/"
             "safety oversight for training events."},
    {"key": "S6", "name": "S6 — Communications", "icon": "📡",
     "desc": "Radio (HAM/GMRS) and digital comms, nets and frequency planning, "
             "and the IT/portal infrastructure that ties everything together."},
]

_SHOP_NAME = {s["key"]: s["name"] for s in SHOP_SIGNUP_CATALOG}


def _held_shop_keys(billets: str | None) -> set[str]:
    """Shop keys ('S1'..'S6') a member currently holds, from primary_billet."""
    if not billets:
        return set()
    return {f"S{n}" for n in re.findall(r"S(\d)\s*:", billets)}


def _is_patched(member: Member | None) -> bool:
    """Patched = currently serving AND past the recruit rank.

    'Patched' is the E-1(RCT) -> E-2(PV2) promotion. Recruits are E-1 with
    status 'recruit'; gating on rank_grade != 'E-1' AND status 'active'
    excludes recruits and inactive/separated/blacklisted members.
    """
    if not member:
        return False
    return (member.status == "active") and ((member.rank_grade or "E-1") != "E-1")


async def _current_member(request: Request, db: AsyncSession) -> Member | None:
    user = get_current_user(request)
    if not user:
        return None
    uname = user.get("username")
    res = await db.execute(select(Member).where(Member.nc_username == uname))
    return res.scalar_one_or_none()

# Shop RBAC: which roles can access which shop
SHOP_ACCESS = {
    "s1": {"s1", "command", "admin"},
    "s2": {"s2", "command", "admin"},
    "s3": {"s3", "command", "admin", "leader"},
    "s4": {"s4", "command", "admin"},
    "s5": {"s5", "command", "admin"},
    "s6": {"s6", "command", "admin"},
}

SHOP_META = {
    "s1": {"name": "S1 — Personnel", "icon": "📋", "has_dashboard": True},
    "s2": {"name": "S2 — Intel & Security", "icon": "🔍", "has_dashboard": False},
    "s3": {"name": "S3 — Ops & Training", "icon": "⚔️", "has_dashboard": True},
    "s4": {"name": "S4 — Logistics", "icon": "📦", "has_dashboard": False},
    "s5": {"name": "S5 — Medical", "icon": "🩹", "has_dashboard": False},
    "s6": {"name": "S6 — Comms", "icon": "📡", "has_dashboard": False},
}


def _check_shop_access(user: dict, shop: str) -> bool:
    """Check if user has access to the given shop."""
    user_roles = set(user.get("roles", []))
    required = SHOP_ACCESS.get(shop, set())
    return bool(user_roles & required)


@router.get("/s1")
@require_auth
async def shop_s1(request: Request):
    """S1 dashboard — redirect to existing pipeline for now."""
    user = get_current_user(request)
    if not _check_shop_access(user, "s1"):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)
    return RedirectResponse("/api/s1/pipeline", status_code=302)


@router.get("/s3")
@require_auth
async def shop_s3(request: Request, db: AsyncSession = Depends(get_db)):
    """S3 Ops & Training dashboard."""
    user = get_current_user(request)
    if not _check_shop_access(user, "s3"):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)
    return templates.TemplateResponse("pages/shop_s3.html", {
        "request": request,
        "user": user,
    })


# ─── Shop sign-up (patched members request to join a shop) ──────────────────

REVIEWER_ROLES = {"command", "admin"}
SHOP_ROLE = {"S1": "s1", "S2": "s2", "S3": "s3", "S4": "s4", "S5": "s5", "S6": "s6"}


def _can_review_shop(user: dict, shop_key: str) -> bool:
    """Shop heads (their shop role) + Command/Admin may review requests."""
    roles = set(user.get("roles", []))
    if roles & REVIEWER_ROLES:
        return True
    return SHOP_ROLE.get(shop_key) in roles


@router.get("/signup", response_class=HTMLResponse)
@require_auth
async def shop_signup_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Shop sign-up page: list shops with descriptions. Patched members only."""
    user = get_current_user(request)
    member = await _current_member(request, db)

    patched = _is_patched(member)
    held = _held_shop_keys(member.primary_billet if member else None)

    # Pending requests by this member (to disable already-requested shops).
    pending_keys: set[str] = set()
    if member:
        pres = await db.execute(
            select(ShopSignupRequest.shop_key).where(
                ShopSignupRequest.member_id == member.id,
                ShopSignupRequest.status == "pending",
            )
        )
        pending_keys = {row[0] for row in pres.all()}

    at_max = len(held) >= MAX_SHOPS

    return templates.TemplateResponse("pages/shop_signup.html", {
        "request": request,
        "user": user,
        "shops": SHOP_SIGNUP_CATALOG,
        "patched": patched,
        "held": held,
        "pending_keys": pending_keys,
        "at_max": at_max,
        "max_shops": MAX_SHOPS,
        "can_review": bool(member and set(user.get("roles", [])) & (REVIEWER_ROLES | set(SHOP_ROLE.values()))),
    })


@router.post("/signup", response_class=HTMLResponse)
@require_auth
async def submit_shop_signup(request: Request, db: AsyncSession = Depends(get_db)):
    """Submit a shop join request. Patched-only, capped at MAX_SHOPS held."""
    member = await _current_member(request, db)
    form = await request.form()
    shop_key = (form.get("shop_key") or "").strip().upper()
    message = (form.get("message") or "").strip() or None

    def _err(msg: str):
        return HTMLResponse(f'<div class="su-flash su-err">{msg}</div>', status_code=400)

    if not _is_patched(member):
        return _err("Shop sign-up is limited to patched members.")
    if shop_key not in _SHOP_NAME:
        return _err("Unknown shop.")

    held = _held_shop_keys(member.primary_billet)
    if shop_key in held:
        return _err(f"You are already a member of {_SHOP_NAME[shop_key]}.")
    if len(held) >= MAX_SHOPS:
        return _err(f"You already hold the maximum of {MAX_SHOPS} shops. "
                    "Drop one before requesting another (see your shop lead or Command).")

    # No duplicate pending request for the same shop.
    dup = await db.execute(
        select(ShopSignupRequest.id).where(
            ShopSignupRequest.member_id == member.id,
            ShopSignupRequest.shop_key == shop_key,
            ShopSignupRequest.status == "pending",
        ).limit(1)
    )
    if dup.first():
        return _err(f"You already have a pending request for {_SHOP_NAME[shop_key]}.")

    req = ShopSignupRequest(
        member_id=member.id, shop_key=shop_key, message=message, status="pending",
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)

    # Notify shop head(s) + Command. Shop head = billet 'Sn:...(Lead)'.
    from app.routes.notifications import create_notification
    rank_abbr = ""
    try:
        from app.constants import RANK_ABBR
        rank_abbr = RANK_ABBR.get(member.rank_grade, "")
    except Exception:
        pass
    applicant = f"{rank_abbr} {member.last_name}".strip()

    lead_prefix = f"{shop_key}:"
    heads_res = await db.execute(select(Member).where(Member.status == "active"))
    notified = set()
    for m in heads_res.scalars().all():
        billets = m.primary_billet or ""
        is_head = any(
            b.strip().startswith(lead_prefix) and "(Lead)" in b
            for b in billets.split(",")
        )
        if is_head and m.id not in notified:
            notified.add(m.id)
            await create_notification(
                db, m.id, "shop",
                f"🏛️ Shop join request — {shop_key}",
                body=f"{applicant} requested to join {_SHOP_NAME[shop_key]}.",
                link="/shops/signup/review", icon="🏛️",
            )
    await db.commit()

    return HTMLResponse(
        f'<div class="su-flash su-ok">Request sent to the {shop_key} lead for review. '
        'You\u2019ll be notified when it\u2019s decided.</div>'
    )


@router.get("/signup/review", response_class=HTMLResponse)
@require_auth
async def shop_signup_review(request: Request, db: AsyncSession = Depends(get_db)):
    """Review pending shop join requests. Shop heads see their shop; Command/Admin see all."""
    user = get_current_user(request)
    roles = set(user.get("roles", []))
    is_cmd = bool(roles & REVIEWER_ROLES)
    # Which shops may this user review?
    reviewable = set(SHOP_ROLE.keys()) if is_cmd else {
        k for k, r in SHOP_ROLE.items() if r in roles
    }
    if not reviewable:
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)

    pending_res = await db.execute(
        select(ShopSignupRequest, Member)
        .join(Member, Member.id == ShopSignupRequest.member_id)
        .where(ShopSignupRequest.status == "pending",
               ShopSignupRequest.shop_key.in_(reviewable))
        .order_by(ShopSignupRequest.created_at.asc())
    )
    recent_res = await db.execute(
        select(ShopSignupRequest, Member)
        .join(Member, Member.id == ShopSignupRequest.member_id)
        .where(ShopSignupRequest.status.in_(["accepted", "declined"]),
               ShopSignupRequest.shop_key.in_(reviewable))
        .order_by(ShopSignupRequest.reviewed_at.desc().nullslast())
        .limit(25)
    )

    from app.constants import RANK_ABBR
    def _row(req, m):
        return {
            "id": req.id, "shop_key": req.shop_key,
            "shop_name": _SHOP_NAME.get(req.shop_key, req.shop_key),
            "member": f"{RANK_ABBR.get(m.rank_grade, '')} {m.last_name}, {m.first_name}".strip(),
            "callsign": m.callsign or "",
            "message": req.message or "",
            "status": req.status,
            "reviewed_by": req.reviewed_by or "",
            "created_at": req.created_at,
        }

    pending = [_row(r, m) for r, m in pending_res.all()]
    recent = [_row(r, m) for r, m in recent_res.all()]

    return templates.TemplateResponse("pages/shop_signup_review.html", {
        "request": request, "user": user,
        "pending": pending, "recent": recent,
    })


@router.post("/signup/{req_id}/accept", response_class=HTMLResponse)
@require_auth
async def accept_shop_signup(request: Request, req_id: int, db: AsyncSession = Depends(get_db)):
    return await _decide_shop_signup(request, req_id, db, accept=True)


@router.post("/signup/{req_id}/decline", response_class=HTMLResponse)
@require_auth
async def decline_shop_signup(request: Request, req_id: int, db: AsyncSession = Depends(get_db)):
    return await _decide_shop_signup(request, req_id, db, accept=False)


async def _decide_shop_signup(request: Request, req_id: int, db: AsyncSession, accept: bool):
    user = get_current_user(request)
    res = await db.execute(select(ShopSignupRequest).where(ShopSignupRequest.id == req_id))
    req = res.scalar_one_or_none()
    if not req:
        return HTMLResponse('<span style="color:#c62828;">Request not found.</span>', status_code=404)
    if not _can_review_shop(user, req.shop_key):
        return HTMLResponse('<span style="color:#c62828;">Not authorized for this shop.</span>', status_code=403)
    if req.status != "pending":
        return HTMLResponse(f'<span style="color:#888;">Already {req.status}.</span>')

    form = await request.form()
    notes = (form.get("notes") or "").strip() or None

    reviewer = user.get("username", "")
    rres = await db.execute(select(Member).where(Member.nc_username == reviewer))
    r_m = rres.scalar_one_or_none()
    if r_m:
        from app.constants import RANK_ABBR
        reviewer = f"{RANK_ABBR.get(r_m.rank_grade, '')} {r_m.last_name}".strip()

    mres = await db.execute(select(Member).where(Member.id == req.member_id))
    member = mres.scalar_one_or_none()

    req.reviewed_by = reviewer
    req.reviewed_at = datetime.utcnow()
    req.review_notes = notes

    from app.routes.notifications import create_notification

    if accept and member:
        # Add the shop to the member's billets, then sync NC shop groups.
        held = _held_shop_keys(member.primary_billet)
        if req.shop_key not in held:
            shop_name = _SHOP_NAME[req.shop_key]
            new_billet = f"{req.shop_key}: {shop_name.split('—',1)[-1].strip()}"
            old_billets = member.primary_billet
            billet_list = [b.strip() for b in (member.primary_billet or "").split(",") if b.strip()]
            billet_list.append(new_billet)
            member.primary_billet = ", ".join(billet_list)
            # Reuse member_edit's tested NC shop-group sync.
            try:
                from app.routes.member_edit import _sync_shop_groups
                await _sync_shop_groups(member.nc_username, old_billets, member.primary_billet)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"shop-group sync failed for {member.nc_username}: {e}")
        req.status = "accepted"
        await db.commit()
        await create_notification(
            db, req.member_id, "shop",
            "✅ Shop request accepted",
            body=f"You\u2019ve been added to {_SHOP_NAME[req.shop_key]} by {reviewer}.",
            link="/profile", icon="✅",
        )
        await db.commit()
        return HTMLResponse(f'<div class="su-decided su-ok">Accepted — {_SHOP_NAME[req.shop_key]} added.</div>')
    else:
        req.status = "declined"
        await db.commit()
        if member:
            await create_notification(
                db, req.member_id, "shop",
                "Shop request declined",
                body=f"Your request to join {_SHOP_NAME[req.shop_key]} was declined by {reviewer}."
                     + (f" Note: {notes}" if notes else ""),
                link="/shops/signup", icon="🚫",
            )
            await db.commit()
        return HTMLResponse('<div class="su-decided su-err">Declined.</div>')


@router.get("/{shop}")
@require_auth
async def shop_placeholder(request: Request, shop: str):
    """Placeholder for shops not yet built."""
    user = get_current_user(request)
    if shop not in SHOP_META:
        return HTMLResponse("<h2>Not Found</h2>", status_code=404)
    if not _check_shop_access(user, shop):
        return HTMLResponse("<h2>Access Denied</h2>", status_code=403)
    meta = SHOP_META[shop]
    return templates.TemplateResponse("pages/shop_placeholder.html", {
        "request": request,
        "user": user,
        "shop": shop,
        "shop_name": meta["name"],
        "shop_icon": meta["icon"],
    })
