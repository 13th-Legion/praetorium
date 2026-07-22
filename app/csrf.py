"""CSRF protection — double-submit cookie pattern.

Why double-submit (not SameSite-only): the session cookie is set
``SameSite=None`` in production to survive the NC OAuth redirect chain
(PP-246). That removes the browser's SameSite CSRF defense, so we add an
explicit token that a cross-site attacker cannot read or forge.

How it works:
  * Every response gets a ``csrftoken`` cookie. It is NOT HttpOnly (JS/HTMX
    must read it) and is ``SameSite=Lax`` + ``Secure`` in prod.
  * Unsafe requests (POST/PUT/PATCH/DELETE) must echo that token back via the
    ``X-CSRF-Token`` header (HTMX sends it globally via ``hx-headers`` on
    <body>) OR a ``csrf_token`` form field. The middleware compares the two.
  * A cross-site page cannot read the victim's ``csrftoken`` cookie (SOP), so
    it cannot produce a matching header/field -> forged POST is rejected 403.

Exemptions:
  * Safe methods (GET/HEAD/OPTIONS/TRACE).
  * External webhooks authenticated by their own signature (PayPal), and the
    auth/OAuth callback paths (no session-authenticated state change; the
    OAuth ``state`` param is the anti-CSRF there).
"""

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse

CSRF_COOKIE = "csrftoken"
CSRF_HEADER = "x-csrf-token"
CSRF_FORM_FIELD = "csrf_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

# Paths exempt from CSRF enforcement (still receive a token cookie):
#  - external webhooks (own signature auth)
#  - OAuth login/callback (OAuth `state` is the CSRF defense; no app session
#    mutation happens through a forgeable same-origin form here)
EXEMPT_PREFIXES = (
    "/api/webhooks/",
    "/auth/",
    "/static/",
    "/nlmedia/",
    "/health",
    "/favicon.ico",
)


def _issue_token() -> str:
    return secrets.token_urlsafe(32)


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, https_only: bool = True):
        super().__init__(app)
        self.https_only = https_only

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        cookie_token = request.cookies.get(CSRF_COOKIE)
        method = request.method.upper()

        exempt = any(path.startswith(p) for p in EXEMPT_PREFIXES)

        if method not in SAFE_METHODS and not exempt:
            sent = request.headers.get(CSRF_HEADER)
            if not sent:
                # Fall back to a hidden form field for native (non-HTMX) <form>
                # POSTs, incl. multipart file-upload forms (uploads here are
                # small PDFs/images). Starlette caches the parsed form on the
                # request, so the downstream handler re-reads it fine.
                ctype = request.headers.get("content-type", "")
                if ("application/x-www-form-urlencoded" in ctype
                        or "multipart/form-data" in ctype):
                    try:
                        form = await request.form()
                        sent = form.get(CSRF_FORM_FIELD)
                    except Exception:
                        sent = None
            if not cookie_token or not sent or not secrets.compare_digest(str(sent), str(cookie_token)):
                msg = "CSRF token missing or invalid. Refresh the page and try again."
                if request.headers.get("HX-Request") == "true":
                    return HTMLResponse(msg, status_code=403)
                return JSONResponse({"detail": msg}, status_code=403)

        response = await call_next(request)

        # Ensure a token cookie exists so the client can echo it back.
        if not cookie_token:
            new_token = _issue_token()
            response.set_cookie(
                CSRF_COOKIE,
                new_token,
                max_age=60 * 60 * 24,
                httponly=False,   # JS/HTMX must read it for the double-submit
                secure=self.https_only,
                samesite="lax",
                path="/",
            )
            # Expose to same-request template rendering (first GET of a session).
            request.state.csrf_token = new_token
        else:
            request.state.csrf_token = cookie_token

        return response
