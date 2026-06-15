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

    # Clear stale session so the OAuth2 state token is written to a fresh
    # cookie — prevents "State token does not match" after session expiry.
    request.session.clear()

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
    """Redirect to Nextcloud OAuth2 login.

    Two routing modes, selected by the ?seed query param:

    DEFAULT (no ?seed) — go DIRECTLY to NC's /oauth2/authorize.
        Fast, single-pass for users ALREADY logged into Nextcloud (NC sees the
        active session and grants immediately). For logged-OUT users this still
        works most of the time, but can intermittently fail with a 403 on
        `POST /login/flow` — see the SameSite note below.

    SEEDED (?seed=1) — route through NC's first-party /index.php/login page
        with redirect_url -> the INTERNAL authorize path. The user types
        credentials on an unambiguously first-party cloud.13thlegion.org page,
        which seeds NC's nc_sameSiteCookie{lax,strict} cookies same-site BEFORE
        the grant POST, so `POST /login/flow` passes NC's SameSite/CSRF check.
        The error/login page sends users here after a failed attempt (auto-retry).

    Why two modes:
        NC's LoginController.showLoginForm() IGNORES redirect_url when the user
        is already logged in (it hard-redirects to the dashboard — see
        core/Controller/LoginController.php). So we cannot send logged-IN users
        through /login. And logged-OUT users initiating cross-site directly at
        /authorize hit NC's SameSiteCookieMiddleware quirk: the grant
        `POST /login/flow` (generateAppPassword) has #[UseSession] but NOT
        #[NoSameSiteCookieRequired], so it requires the lax SameSite cookie;
        when the browser omits it on the cross-site-initiated POST, NC returns
        403 "Access forbidden". Seeding via /login fixes that for logged-out users
        while the direct path stays fast for logged-in users.
    """
    from urllib.parse import urlencode, quote

    authorize_url = _build_authorize_url(request)
    seed = request.query_params.get("seed")

    if seed:
        # SEEDED PATH: hand the user to NC's own first-party login page so the
        # SameSite cookies are established same-site before the grant POST.
        # redirect_url must be an INTERNAL NC path (LoginController only honors
        # internal redirect targets); we strip the origin from authorize_url.
        #
        # _build_authorize_url() just cleared the session, which wiped any
        # _seed_retry marker. Re-arm it so that if THIS seeded attempt also
        # fails, the callback's loop guard trips and shows a real error instead
        # of looping back into /auth/login?seed=1 forever.
        request.session["_seed_retry"] = True
        internal_authorize = authorize_url[len(settings.nc_url):] if authorize_url.startswith(settings.nc_url) else authorize_url
        login_url = f"{settings.nc_url}/index.php/login?redirect_url={quote(internal_authorize, safe='')}"
        return RedirectResponse(url=login_url, status_code=302)

    # DEFAULT PATH: direct to /authorize (fast for already-logged-in users).
    return RedirectResponse(url=authorize_url, status_code=302)


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
        # Guard against loops: only auto-retry if we haven't already seeded.
        if not request.session.get("_seed_retry"):
            request.session["_seed_retry"] = True
            return RedirectResponse(url="/auth/login?seed=1", status_code=302)
        # Already retried via the seeded path — show the error for real.
        request.session.pop("_seed_retry", None)
        return templates.TemplateResponse("pages/login.html", {
            "request": request,
            "error": f"OAuth error: {str(e)}",
        })

    # Success — clear any retry marker.
    request.session.pop("_seed_retry", None)

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
