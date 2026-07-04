"""Authentication routes — NC OAuth2 SSO login/logout."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from authlib.integrations.starlette_client import OAuth

from config import get_settings
from app.auth import fetch_nc_groups, map_groups_to_roles

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

# OAuth2 client setup
oauth = OAuth()
oauth.register(
    name="nextcloud",
    client_id=settings.nc_client_id,
    client_secret=settings.nc_client_secret,
    authorize_url=f"{settings.nc_url}/index.php/apps/oauth2/authorize",
    access_token_url=f"{settings.nc_url}/index.php/apps/oauth2/api/v1/token",
    userinfo_endpoint=f"{settings.nc_url}/ocs/v2.php/cloud/user?format=json",
    client_kwargs={"scope": "openid profile email"},
    userinfo_compliance_fix=lambda client, user_class, data: data.get("ocs", {}).get("data", data),
)


def _build_authorize_url(request: "Request") -> str:
    """Clear the session, mint a fresh OAuth2 state, and return the NC
    /authorize URL. Shared by both the direct path and the seeded path."""
    import secrets
    import time
    from urllib.parse import urlencode

    # Do NOT clear the whole session here. Clearing rotates the session cookie
    # mid-flow; the browser (esp. a pre-existing tab with an older cookie) may
    # then send back a STALE cookie on the /auth/callback redirect, so the
    # freshly-minted state isn't found -> authlib raises "mismatching_state"
    # -> "State token does not match". Instead, keep the session stable and only
    # prune stale/expired OAuth2 state entries so they don't accumulate. Writing
    # the new state into the SAME session cookie means the browser returns it.
    now = time.time()
    stale_state_keys = [
        k for k, v in list(request.session.items())
        if k.startswith("_state_nextcloud_")
        and (not isinstance(v, dict) or v.get("exp", 0) < now)
    ]
    for k in stale_state_keys:
        request.session.pop(k, None)
    # Hard cap: never keep more than a handful of pending states.
    pending = [k for k in request.session if k.startswith("_state_nextcloud_")]
    if len(pending) > 4:
        for k in pending[:-4]:
            request.session.pop(k, None)

    state = secrets.token_urlsafe(32)
    redirect_uri = f"{settings.app_url}/auth/callback"
    # Store state in the same format authlib expects on callback:
    # key = _state_{name}_{state}, value = {data: {state, redirect_uri}, exp}
    request.session[f"_state_nextcloud_{state}"] = {
        "data": {"state": state, "redirect_uri": redirect_uri},
        "exp": time.time() + 900,
    }

    authorize_params = urlencode({
        "response_type": "code",
        "client_id": settings.nc_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return f"{settings.nc_url}/index.php/apps/oauth2/authorize?{authorize_params}"


@router.get("/login")
async def login(request: Request):
    """Redirect to Nextcloud OAuth2 login (ALWAYS via the seeded first-party path).

    We ALWAYS route through NC's first-party /index.php/login page with
    redirect_url -> the INTERNAL /oauth2/authorize path. This seeds NC's
    nc_sameSiteCookie{lax,strict} cookies same-site BEFORE the grant POST, so
    `POST /login/flow` (generateAppPassword) passes NC's SameSite/CSRF check
    instead of intermittently returning 403 "Access forbidden".

    History: we previously sent ALREADY-logged-in users straight to /authorize
    (the "direct path") on the assumption that NC's LoginController ignores
    redirect_url for authenticated sessions. That assumption is FALSE on
    NC 32.x: an authenticated hit to /index.php/login?redirect_url=<internal>
    honors the internal redirect and lands cleanly on the grant page WITH the
    SameSite cookies seeded. The direct path was the sole cause of the
    intermittent "Forbidden" on grant (a stale-session grant POST dropped the
    lax SameSite cookie -> 403). Always-seeding fixes it for both logged-in and
    logged-out users. Verified live 2026-06-21.

    The ?seed query param is retained for backward-compat (the error page and
    callback retry still link to ?seed=1) but is now a no-op distinction:
    every path seeds.
    """
    from urllib.parse import quote

    authorize_url = _build_authorize_url(request)

    # Loop guard: track how many times we've bounced through login for the
    # CURRENT flow. _build_authorize_url no longer clears the session, so this
    # counter now survives the redirect (the old boolean got wiped every time).
    # The callback increments/checks this to surface a real error instead of
    # looping forever on a persistent state mismatch.
    request.session["_login_attempts"] = request.session.get("_login_attempts", 0) + 1

    # redirect_url must be an INTERNAL NC path (LoginController only honors
    # internal redirect targets); strip the origin from authorize_url.
    internal_authorize = authorize_url[len(settings.nc_url):] if authorize_url.startswith(settings.nc_url) else authorize_url
    login_url = f"{settings.nc_url}/index.php/login?redirect_url={quote(internal_authorize, safe='')}"
    return RedirectResponse(url=login_url, status_code=302)


@router.get("/callback")
async def callback(request: Request):
    """Handle OAuth2 callback from Nextcloud."""
    try:
        token = await oauth.nextcloud.authorize_access_token(request)
    except Exception as e:
        # The OAuth grant failed. The most common cause is NC's intermittent
        # SameSite 403 on `POST /login/flow` for logged-OUT users initiating the
        # flow cross-site (see /auth/login docstring). That derails the flow and
        # we land here with a missing/mismatched state. Retry ONCE through the
        # seeded path (NC first-party /login) which establishes the SameSite
        # cookies same-site and reliably completes the grant.
        #
        # Guard against loops: allow a bounded number of retries through the
        # seeded path, then surface the error for real. _login_attempts is
        # incremented in /auth/login and now persists across the redirect
        # (session is no longer cleared), so this counter is reliable.
        if request.session.get("_login_attempts", 0) < 2:
            return RedirectResponse(url="/auth/login?seed=1", status_code=302)
        # Exhausted retries — clear the counter and show the error for real.
        request.session.pop("_login_attempts", None)
        return templates.TemplateResponse("pages/login.html", {
            "request": request,
            "error": f"OAuth error: {str(e)}",
        })

    # Success — clear the retry counter.
    request.session.pop("_login_attempts", None)

    # Fetch user info using the access token
    resp = await oauth.nextcloud.get(
        f"{settings.nc_url}/ocs/v2.php/cloud/user?format=json",
        token=token,
        headers={"OCS-APIRequest": "true"},
    )
    userdata = resp.json().get("ocs", {}).get("data", {})

    username = userdata.get("id", "")
    display_name = userdata.get("displayname", username)
    email = userdata.get("email", "")
    nc_groups = userdata.get("groups", [])

    # Map NC groups to portal roles
    roles = map_groups_to_roles(nc_groups)

    # Persist portal_roles to DB so background queries (notifications, etc.) can use them
    import json
    from app.database import async_session
    from app.models.member import Member
    from sqlalchemy import select, update
    async with async_session() as db:
        result = await db.execute(select(Member.id).where(Member.nc_username == username))
        row = result.first()
        if row:
            await db.execute(
                update(Member).where(Member.id == row[0]).values(portal_roles=json.dumps(roles))
            )
            await db.commit()

    # Store user in session
    request.session["user"] = {
        "username": username,
        "display_name": display_name,
        "email": email,
        "groups": nc_groups,
        "roles": roles,
    }

    return RedirectResponse(url="/", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)
