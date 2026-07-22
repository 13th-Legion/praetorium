"""Project Praetorium — FastAPI application entry point."""

# TODO: Add CSRF protection middleware (e.g., fastapi-csrf-protect)
# See AUDIT_2026-04-01.md finding 2.4

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select

from config import get_settings
from app.database import engine, Base, async_session
from app.routes import auth, settings as settings_route, dashboard, health, debug, roster, profile, profile_summary, tlas, s1_admin, events, announcements, member_edit, training_claims, training_library, awards, contact_edit, shops, s3_ops, ops_console, team_manage, notifications, elections, paypal_webhook, attendance_analytics, checkout, conduct, promotions, donate, weapons_qual, tradoc_admin, aars, recruiting_analytics, newsletter, chain_of_command, ribbons_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    import asyncio
    # Prime the ranks service sync snapshot so the first requests have live rank
    # data (never blank) even before any async access warms it.
    try:
        from app.services import ranks as _ranks_warm
        _ranks_warm.warm()
        from app.services import shops as _shops_warm
        _shops_warm.warm()
        from app.services import settings_store as _ss_warm
        _ss_warm.warm()
        from app.services import taxonomies as _tax_warm
        _tax_warm.warm()
        from app.services import nc_rooms as _ncr_warm
        _ncr_warm.warm()
    except Exception as _e:
        import logging as _lg
        _lg.getLogger("uvicorn.error").warning(f"service warm skipped: {_e}")
    from app.newsletter_scheduler import newsletter_scheduler_loop
    # Seed newsletter section templates (idempotent insert-if-missing).
    try:
        from app.database import async_session as _async_session
        from app.newsletter_sections_seed import seed_section_templates
        async with _async_session() as _db:
            _n = await seed_section_templates(_db)
            if _n:
                import logging as _lg
                _lg.getLogger("uvicorn.error").info(f"Seeded {_n} newsletter section templates")
    except Exception as _e:
        import logging as _lg
        _lg.getLogger("uvicorn.error").warning(f"Newsletter section seed skipped: {_e}")
    _nl_task = asyncio.create_task(newsletter_scheduler_loop())
    try:
        yield
    finally:
        _nl_task.cancel()
        try:
            await _nl_task
        except (asyncio.CancelledError, Exception):
            pass
        await engine.dispose()


settings = get_settings()

# ─── Error tracking (optional) ───────────────────────────────────────────────
# Initializes only when SENTRY_DSN is set, so local/dev is unaffected. Captures
# unhandled exceptions with request context (audit finding: no error tracking).
import os as _os_sentry
import logging as _logging_sentry
_SENTRY_DSN = _os_sentry.getenv("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            traces_sample_rate=float(_os_sentry.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            environment="debug" if settings.debug else "production",
            integrations=[FastApiIntegration()],
        )
        _logging_sentry.getLogger("uvicorn.error").info("Sentry error tracking enabled")
    except Exception as _se:
        _logging_sentry.getLogger("uvicorn.error").warning(f"Sentry init skipped: {_se}")


# ─── Contact Verification Middleware ─────────────────────────────────────────

VERIFY_BYPASS = {"/auth/", "/verify-contact", "/static/", "/nlmedia/", "/health", "/api/docs", "/favicon.ico"}


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


# ─── Guest Read-Only Middleware ───────────────────────────────────────────────

# Methods that mutate state. Guests are blocked from all of these.
GUEST_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Paths a guest is still allowed to POST to (auth/session lifecycle only).
GUEST_WRITE_ALLOW = ("/auth/", "/logout")


class GuestReadOnlyMiddleware(BaseHTTPMiddleware):
    """Read-only enforcement for guest accounts (other-unit visitors).

    Guests (portal role == 'guest') can view every page and every editing
    interface, but any write request (POST/PUT/PATCH/DELETE) is rejected with a
    friendly 403 instead of persisting. This is the single server-side safety
    net so we don't have to audit every write endpoint individually.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in GUEST_WRITE_METHODS:
            user = request.session.get("user")
            if user and "guest" in set(user.get("roles", [])):
                path = request.url.path
                if not any(path.startswith(p) for p in GUEST_WRITE_ALLOW):
                    msg = ("Guest accounts are read-only. You can look around every "
                           "part of the portal, but changes can't be saved from a "
                           "guest account.")
                    # HTMX requests get a plain-text 403 the UI can surface inline;
                    # everything else gets a simple styled page.
                    if request.headers.get("HX-Request") == "true":
                        return HTMLResponse(msg, status_code=403)
                    accept = request.headers.get("accept", "")
                    if "application/json" in accept:
                        return JSONResponse({"detail": msg}, status_code=403)
                    return HTMLResponse(
                        f"<!doctype html><html><head><title>Read-only</title>"
                        f"<style>body{{font-family:system-ui,sans-serif;background:#1a1a1a;"
                        f"color:#ddd;display:flex;align-items:center;justify-content:center;"
                        f"height:100vh;margin:0;text-align:center;}}"
                        f".box{{max-width:460px;padding:32px;border:1px solid #333;"
                        f"border-radius:10px;background:#222;}}h1{{font-size:20px;"
                        f"margin:0 0 12px;}}p{{color:#aaa;line-height:1.5;}}"
                        f"a{{color:#8ab4f8;}}</style></head><body><div class='box'>"
                        f"<h1>👁️ Read-only guest account</h1><p>{msg}</p>"
                        f"<p><a href='javascript:history.back()'>← Go back</a></p>"
                        f"</div></body></html>",
                        status_code=403,
                    )
        return await call_next(request)


# ─── Display Name & Role Refresh Middleware ───────────────────────────────────

DISPLAY_REFRESH_INTERVAL = 15 * 60  # 15 minutes in seconds
DISPLAY_REFRESH_BYPASS = {"/auth/", "/static/", "/nlmedia/", "/health", "/favicon.ico"}


class DisplayRefreshMiddleware(BaseHTTPMiddleware):
    """Periodically refresh display_name and roles from DB/NC every 15 minutes."""

    async def dispatch(self, request: Request, call_next):
        import time
        path = request.url.path

        # Skip for non-session paths
        if any(path.startswith(p) for p in DISPLAY_REFRESH_BYPASS):
            return await call_next(request)

        user = request.session.get("user")
        if user:
            now = int(time.time())
            last_refresh = request.session.get("_display_refreshed_at", 0)

            if (now - last_refresh) >= DISPLAY_REFRESH_INTERVAL:
                username = user.get("username", "")
                if username:
                    # Refresh display_name from Member DB
                    from app.models.member import Member
                    try:
                        async with async_session() as db:
                            result = await db.execute(
                                select(Member).where(Member.nc_username == username)
                            )
                            member = result.scalar_one_or_none()
                            if member:
                                request.session["user"]["display_name"] = member.display_name
                    except Exception:
                        pass  # Don't break requests on refresh failure

                    # Refresh roles from NC groups
                    try:
                        from app.auth import fetch_nc_groups, map_groups_to_roles
                        nc_groups = await fetch_nc_groups(username)
                        roles = map_groups_to_roles(nc_groups)
                        request.session["user"]["groups"] = nc_groups
                        request.session["user"]["roles"] = roles
                    except Exception:
                        pass  # Don't break requests on NC fetch failure

                    request.session["_display_refreshed_at"] = now

        return await call_next(request)


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
from app.csrf import CSRFMiddleware
app.add_middleware(ContactVerifyMiddleware)
app.add_middleware(GuestReadOnlyMiddleware)
app.add_middleware(DisplayRefreshMiddleware)
# CSRF double-submit — added before SessionMiddleware so it sits INSIDE the
# session (session cookie is available), and runs on every request to issue /
# validate the csrftoken cookie. Defends even with SameSite=None sessions.
app.add_middleware(CSRFMiddleware, https_only=not settings.debug)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_max_age,
    https_only=not settings.debug,
    # OAuth login uses a cross-context redirect chain (Praetorium -> NC grant ->
    # password -> grant -> /auth/callback). With the Starlette default
    # same_site="lax", the session cookie carrying the authlib OAuth `state` can
    # be dropped on those cross-site POST->redirect hops (notably on mobile with a
    # stale token requiring a second grant), producing "State token does not
    # match" (PP-246). "none" keeps the cookie on the redirect; it requires the
    # Secure attribute, which https_only provides in production (debug=False).
    # Falls back to "lax" in local debug where https_only is off (Secure absent).
    same_site="none" if not settings.debug else "lax",
)

# ─── Global exception handler ────────────────────────────────────────────────
# Unhandled exceptions were surfacing as bare 500s with no capture. Log with
# stack + return a clean response (HTMX-aware). Sentry (if enabled) captures
# automatically via its integration; this guarantees a log line either way.
_exc_logger = _logging_sentry.getLogger("praetorium.errors")


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    _exc_logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    msg = "Something went wrong on our end. It's been logged."
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse(f'<div style="color:#ef5350;font-size:13px;">⚠️ {msg}</div>', status_code=500)
    return JSONResponse({"detail": msg}, status_code=500)


# Favicon at root (browsers always request /favicon.ico)
from fastapi.responses import FileResponse

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("app/static/img/favicon.ico")

# Static files & templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Public (unauthenticated) newsletter media — inline images embedded in sent
# emails must be fetchable by remote mail clients (Gmail image proxy, etc.).
# Stored in the mounted /app/data/newsletter volume (deploy-safe).
import os as _os
_nlmedia_dir = _os.getenv("NEWSLETTER_DATA_DIR", "/app/data/newsletter")
_os.makedirs(_os.path.join(_nlmedia_dir, "images"), exist_ok=True)
_os.makedirs(_os.path.join(_nlmedia_dir, "attachments"), exist_ok=True)
app.mount("/nlmedia", StaticFiles(directory=_nlmedia_dir), name="nlmedia")
templates = Jinja2Templates(directory="app/templates")

# Custom Jinja2 filters
from datetime import datetime as _dt, timezone as _tz
from zoneinfo import ZoneInfo as _ZoneInfo
_CT = _ZoneInfo("America/Chicago")
def _timestamp_fmt(epoch_secs):
    """Convert Unix epoch seconds to a Central-Time readable date string.
    Interpret the epoch as UTC then convert to CT (explicit tz), rather than
    relying on the process's local timezone — so display is stable regardless
    of container TZ (mirrors roster.py)."""
    try:
        return (_dt.fromtimestamp(int(epoch_secs), tz=_tz.utc)
                .astimezone(_CT).strftime("%b %d, %Y %I:%M %p"))
    except Exception:
        return "Unknown"
templates.env.filters["timestamp_fmt"] = _timestamp_fmt

# Routes
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(roster.router)
app.include_router(chain_of_command.router)
app.include_router(ribbons_admin.router)
app.include_router(profile.router)
app.include_router(profile_summary.router)
app.include_router(tlas.router)
app.include_router(s1_admin.router)
app.include_router(events.router)
app.include_router(announcements.router)
app.include_router(member_edit.router)
app.include_router(training_claims.router)
app.include_router(training_library.router)
app.include_router(awards.router)
app.include_router(contact_edit.router)
app.include_router(shops.router)
app.include_router(s3_ops.router)
app.include_router(ops_console.router)
app.include_router(team_manage.router)
app.include_router(notifications.router)
app.include_router(elections.router)
app.include_router(paypal_webhook.router)
app.include_router(attendance_analytics.router)
app.include_router(recruiting_analytics.router)
app.include_router(checkout.router)
app.include_router(debug.router)
app.include_router(settings_route.router)
app.include_router(conduct.router)
app.include_router(promotions.router)
app.include_router(donate.router)
app.include_router(weapons_qual.router)
app.include_router(tradoc_admin.router)
app.include_router(aars.router)
app.include_router(newsletter.router)


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
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("pages/login.html", {"request": request})
