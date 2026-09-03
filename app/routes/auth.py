"""Authentication routes — NC OAuth2 SSO login/logout."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from authlib.integrations.starlette_client import OAuth

from config import get_settings
from app.auth import fetch_nc_groups, map_groups_to_roles

settings = get_settings()
logger = logging.getLogger(__name__)
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
    """Mint a fresh OAuth2 state and return the NC /authorize URL. Shared by
    both the direct path and the seeded path.

    SINGLE-FLIGHT (2026-08-03): NC 33 stores exactly ONE grant stateToken per
    NC session. Every hit to NC's /apps/oauth2/authorize OVERWRITES it. So if
    the portal fires /authorize twice for one login (bfcache resurrecting a
    stale flow, browser prefetch, double-nav, or our own callback retry), the
    SECOND authorize invalidates the grant page the user is actually looking
    at -> tapping "Grant access" 403s on a now-orphaned stateToken. To prevent
    the double-fire we reuse an in-flight authorize URL if one was minted for
    this session within the last IN_FLIGHT_WINDOW seconds instead of minting a
    brand-new state (and thus a brand-new NC grant flow).
    """
    import secrets
    import time
    from urllib.parse import urlencode

    IN_FLIGHT_WINDOW = 15  # seconds
    inflight = request.session.get("_authorize_inflight")
    if isinstance(inflight, dict) and (time.time() - inflight.get("ts", 0)) < IN_FLIGHT_WINDOW:
        url = inflight.get("url")
        if url:
            return url

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
    authorize_url = f"{settings.nc_url}/index.php/apps/oauth2/authorize?{authorize_params}"
    # Remember this in-flight authorize so a rapid second /auth/login (bfcache,
    # prefetch, double-nav) reuses it instead of minting a fresh NC grant flow.
    request.session["_authorize_inflight"] = {"url": authorize_url, "ts": time.time()}
    return authorize_url


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
    resp = RedirectResponse(url=login_url, status_code=302)
    # no-store: keep Brave/Safari bfcache from resurrecting a spent login flow
    # and silently re-firing /authorize (which overwrites NC's grant stateToken
    # -> 403 on Grant). See _build_authorize_url single-flight note.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@router.get("/callback")
async def callback(request: Request):
    """Handle OAuth2 callback from Nextcloud."""
    try:
        token = await oauth.nextcloud.authorize_access_token(request)
    except Exception as e:
        # DO NOT auto-retry by bouncing back to /auth/login. That fired a SECOND
        # /authorize which overwrote NC 33's single per-session grant stateToken,
        # invalidating the grant page the user was looking at -> the tap on
        # "Grant access" then 403'd (the recurring post-NC-33 Forbidden, 5th
        # variant, 2026-08-03). One login = exactly one /authorize = one
        # stateToken. Instead of an automatic re-authorize, clear the in-flight
        # flow and render a login page with a manual "Sign in again" link. The
        # user's next click mints a single clean flow.
        request.session.pop("_login_attempts", None)
        request.session.pop("_authorize_inflight", None)
        resp = templates.TemplateResponse("pages/login.html", {
            "request": request,
            "error": "Your sign-in session expired or was interrupted. Please sign in again.",
            "detail": str(e),
        })
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp

    # Success — clear the in-flight flow + retry counter.
    request.session.pop("_login_attempts", None)
    request.session.pop("_authorize_inflight", None)

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

    # Revoke the NC OAuth2 token now that we're done with it (2026-09-03).
    # The portal uses this token EXACTLY ONCE — the /cloud/user call above.
    # Groups come from the admin provisioning creds (fetch_nc_groups) and the
    # portal session is a signed cookie, so the token is dead weight from here
    # on. Left alone, NC keeps it FOREVER: every single login minted another
    # permanent oc_authtoken row, which is how that table got to 90% "Project
    # Praetorium" (1,807 of 2,015 rows). That bloat doesn't lock anyone out by
    # itself, but it buries the real device tokens and made diagnosing SGT
    # Moreno's stale-token 401 loop far harder than it should have been.
    #
    # DELETE /ocs/v2.php/core/apppassword kills the token that authenticated
    # the request — verified against NC 33 w/ a bearer token: 200 on delete,
    # 401 on reuse, main password unaffected.
    #
    # BEST-EFFORT ONLY. This runs on the login path, so a hiccup here must
    # never cost a member their session — same defensive posture as the
    # fetch_nc_groups fallback below. Worst case we leak one token like before.
    try:
        await oauth.nextcloud.delete(
            f"{settings.nc_url}/ocs/v2.php/core/apppassword",
            token=token,
            headers={"OCS-APIRequest": "true"},
        )
    except Exception:
        logger.warning(
            "NC OAuth2 token revoke failed for %s (non-fatal, login continues)",
            username or "<unknown>",
            exc_info=True,
        )

    # IMPORTANT: the self-scoped OCS endpoint (/cloud/user) returns only a
    # partial group list for non-privileged users, so shop groups like
    # "[S-1] Admin" get dropped -> wrong portal roles -> nav menu hides shops
    # even though direct links work (routes 403-gate on the same roles, but the
    # 15-min DisplayRefresh middleware heals them mid-session, masking the bug).
    # Fetch the authoritative group list via the admin provisioning API instead
    # (same call the refresh middleware uses). Fall back to token groups if it
    # fails so login never breaks on an NC hiccup.
    try:
        nc_groups = await fetch_nc_groups(username)
    except Exception:
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
