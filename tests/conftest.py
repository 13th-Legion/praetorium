"""Shared pytest fixtures for the Praetorium portal test suite."""

import os
import sys
import json
import base64

# Set env vars BEFORE any app imports so get_settings() picks them up.
# debug=True → SessionMiddleware uses https_only=False → cookies work over http://testserver
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "pytest-test-secret-key-not-for-prod")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://praetorium:praetorium@db:5432/praetorium")
os.environ.setdefault("NC_URL", "https://cloud.13thlegion.org")
os.environ.setdefault("NC_CLIENT_ID", "test-client-id")
os.environ.setdefault("NC_CLIENT_SECRET", "test-client-secret")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import itsdangerous
from starlette.testclient import TestClient

# Import app after env vars are set
from app.main import app


# ─── Session Cookie Helper ────────────────────────────────────────────────────

def make_session_cookie(session_data: dict) -> str:
    """Create a signed Starlette session cookie matching SessionMiddleware format.

    Starlette SessionMiddleware serialises sessions as:
      b64encode(json_bytes) → TimestampSigner.sign → cookie value
    """
    secret_key = os.environ.get("SECRET_KEY", "pytest-test-secret-key-not-for-prod")
    signer = itsdangerous.TimestampSigner(secret_key)
    data = json.dumps(session_data).encode("utf-8")
    data = base64.b64encode(data)
    return signer.sign(data).decode("utf-8")


# ─── Fake User ────────────────────────────────────────────────────────────────

FAKE_USER = {
    "username": "test.soldier",
    "display_name": "CPT Testuser",
    "email": "test@13thlegion.org",
    "groups": ["Command", "admin"],
    "roles": ["command", "admin"],
}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def client():
    """Unauthenticated TestClient. Does NOT follow redirects by default."""
    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        yield c


@pytest.fixture(scope="session")
def auth_client():
    """Authenticated TestClient with a fake command/admin session.

    Injects ``contact_verified=True`` so ContactVerifyMiddleware passes
    through without a DB call.  Route-level DB queries still need a running
    database — use this fixture only in @pytest.mark.integration tests.
    """
    session_data = {
        "user": FAKE_USER,
        "contact_verified": True,
    }
    cookie_value = make_session_cookie(session_data)
    with TestClient(
        app,
        raise_server_exceptions=False,
        follow_redirects=False,
    ) as c:
        c.cookies.set("session", cookie_value, domain="testserver", path="/")
        yield c
