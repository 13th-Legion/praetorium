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


@router.get("/login")
async def login(request: Request):
    """Redirect to Nextcloud OAuth2 login."""
    # Clear stale session so the OAuth2 state token is written to a fresh
    # cookie — prevents "State token does not match" after session expiry.
    request.session.clear()
    redirect_uri = f"{settings.app_url}/auth/callback"
    return await oauth.nextcloud.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request):
    """Handle OAuth2 callback from Nextcloud."""
    try:
        token = await oauth.nextcloud.authorize_access_token(request)
    except Exception as e:
        return templates.TemplateResponse("pages/login.html", {
            "request": request,
            "error": f"OAuth error: {str(e)}",
        })

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
