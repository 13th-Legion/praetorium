"""Project Praetorium — FastAPI application entry point."""

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select

from config import get_settings
from app.database import engine, Base, async_session
from app.routes import auth, dashboard, health, debug, roster, profile, profile_summary, tlas, s1_admin, events, announcements, member_edit, training_claims, awards, contact_edit, shops, s3_ops, ops_console, team_manage


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    yield
    await engine.dispose()


settings = get_settings()


# ─── Contact Verification Middleware ─────────────────────────────────────────

VERIFY_BYPASS = {"/auth/", "/verify-contact", "/static/", "/health", "/api/docs", "/favicon.ico"}


class ContactVerifyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip check for bypass paths
        if any(path.startswith(p) for p in VERIFY_BYPASS):
            return await call_next(request)

        user = request.session.get("user")
        if not user:
            return await call_next(request)

        # Check if already verified this session
        if request.session.get("contact_verified"):
            return await call_next(request)

        # Look up member and check contact_verified_at
        from app.models.member import Member
        username = user.get("username", "")
        if username:
            async with async_session() as db:
                result = await db.execute(
                    select(Member.contact_verified_at).where(Member.nc_username == username)
                )
                row = result.first()
                if row and row[0] is not None:
                    # Already verified in DB — cache in session
                    request.session["contact_verified"] = True
                    return await call_next(request)
                elif row is None:
                    # No member record — skip verification (admin/bot accounts)
                    return await call_next(request)

        # Not verified — redirect to verification page
        return RedirectResponse(url="/verify-contact", status_code=302)


# ─── App Setup ───────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
    lifespan=lifespan,
)

# Middleware — order matters: last added = outermost = runs first
# ContactVerifyMiddleware added first → inner (has session access)
# SessionMiddleware added last → outermost (provides session to everything inside)
app.add_middleware(ContactVerifyMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_max_age,
    https_only=not settings.debug,
)

# Favicon at root (browsers always request /favicon.ico)
from fastapi.responses import FileResponse

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("app/static/img/favicon.ico")

# Static files & templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Custom Jinja2 filters
from datetime import datetime as _dt
def _timestamp_fmt(epoch_secs):
    """Convert Unix epoch seconds to readable date string."""
    try:
        return _dt.fromtimestamp(int(epoch_secs)).strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return "Unknown"
templates.env.filters["timestamp_fmt"] = _timestamp_fmt

# Routes
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(roster.router)
app.include_router(profile.router)
app.include_router(profile_summary.router)
app.include_router(tlas.router)
app.include_router(s1_admin.router)
app.include_router(events.router)
app.include_router(announcements.router)
app.include_router(member_edit.router)
app.include_router(training_claims.router)
app.include_router(awards.router)
app.include_router(contact_edit.router)
app.include_router(shops.router)
app.include_router(s3_ops.router)
app.include_router(ops_console.router)
app.include_router(team_manage.router)
app.include_router(debug.router)


# ─── Contact Verification Routes ────────────────────────────────────────────

@app.get("/verify-contact")
async def verify_contact_page(request: Request):
    """Show contact verification form."""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)

    from app.models.member import Member
    username = user.get("username", "")
    async with async_session() as db:
        result = await db.execute(select(Member).where(Member.nc_username == username))
        member = result.scalar_one_or_none()

    if not member:
        return RedirectResponse(url="/", status_code=302)

    return templates.TemplateResponse("pages/verify_contact.html", {
        "request": request,
        "user": user,
        "member": member,
    })


@app.post("/verify-contact")
async def submit_verify_contact(
    request: Request,
    phone: str = Form(...),
    address: str = Form(...),
    city: str = Form(...),
    state: str = Form("TX"),
    zip_code: str = Form(...),
    personal_email: str = Form(""),
    emergency_contact: str = Form(...),
    emergency_phone: str = Form(...),
):
    """Process contact verification form."""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)

    from app.models.member import Member
    username = user.get("username", "")

    async with async_session() as db:
        result = await db.execute(select(Member).where(Member.nc_username == username))
        member = result.scalar_one_or_none()
        if not member:
            return RedirectResponse(url="/", status_code=302)

        member.phone = phone.strip()
        member.address = address.strip()
        member.city = city.strip()
        member.state = state.strip().upper()
        member.zip_code = zip_code.strip()
        member.personal_email = personal_email.strip() or None
        member.emergency_contact = emergency_contact.strip()
        member.emergency_phone = emergency_phone.strip()
        member.contact_verified_at = datetime.utcnow()

        await db.commit()

    request.session["contact_verified"] = True
    return RedirectResponse(url="/", status_code=302)


# ─── Index ───────────────────────────────────────────────────────────────────

@app.get("/")
async def index(request: Request):
    """Landing page — redirect to dashboard if authed, login if not."""
    user = request.session.get("user")
    if user:
        groups = set(user.get("groups", []))
        return templates.TemplateResponse("pages/dashboard.html", {
            "request": request,
            "user": user,
            "is_command": bool(groups & {"Command", "admin"}),
        })
    return templates.TemplateResponse("pages/login.html", {"request": request})
