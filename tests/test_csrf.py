"""Phase 1 — CSRF double-submit middleware.

Guards the critical CSRF fix: issue token, block missing/mismatched token,
allow matching header or form field, exempt webhook + auth paths.
"""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.csrf import CSRFMiddleware, CSRF_COOKIE

pytestmark = pytest.mark.unit


async def _ok(request):
    return PlainTextResponse("ok")


def _make_app():
    app = Starlette(routes=[
        Route("/", _ok),
        Route("/do", _ok, methods=["POST"]),
        Route("/api/webhooks/paypal", _ok, methods=["POST"]),
        Route("/auth/login", _ok, methods=["POST"]),
    ])
    app.add_middleware(CSRFMiddleware, https_only=False)
    return app


def test_get_issues_token_cookie():
    with TestClient(_make_app()) as c:
        r = c.get("/")
        assert r.cookies.get(CSRF_COOKIE)


def test_post_without_token_blocked():
    with TestClient(_make_app()) as c:
        assert c.post("/do").status_code == 403


def test_post_with_matching_header_allowed():
    with TestClient(_make_app()) as c:
        c.get("/")
        tok = c.cookies.get(CSRF_COOKIE)
        assert c.post("/do", headers={"X-CSRF-Token": tok}).status_code == 200


def test_post_with_wrong_token_blocked():
    with TestClient(_make_app()) as c:
        c.get("/")
        assert c.post("/do", headers={"X-CSRF-Token": "WRONG"}).status_code == 403


def test_post_with_form_field_allowed():
    with TestClient(_make_app()) as c:
        c.get("/")
        tok = c.cookies.get(CSRF_COOKIE)
        assert c.post("/do", data={"csrf_token": tok}).status_code == 200


def test_webhook_path_exempt():
    with TestClient(_make_app()) as c:
        assert c.post("/api/webhooks/paypal").status_code == 200


def test_auth_path_exempt():
    with TestClient(_make_app()) as c:
        assert c.post("/auth/login").status_code == 200
