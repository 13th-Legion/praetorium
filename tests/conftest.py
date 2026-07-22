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
import pytest_asyncio
import itsdangerous
from starlette.testclient import TestClient

# Import app after env vars are set
from app.main import app

# Register all models against Base metadata so the test schema can be built.
import app.models  # noqa: F401
from app.database import Base


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


# ─── Async test database ──────────────────────────────────────────────
#
# TEST_DATABASE_URL lets CI point at an ephemeral Postgres
# (postgresql+asyncpg://...). Locally it defaults to in-memory SQLite so the
# suite runs with no external services. Each test gets a fresh schema built
# from Base.metadata and a session; routes that call the module-level
# app.database.async_session are redirected to the test sessionmaker via the
# `patch_global_session` fixture.

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:"
)


@pytest_asyncio.fixture
async def db_engine():
    from sqlalchemy.ext.asyncio import create_async_engine
    # For in-memory SQLite, a single shared connection must back the whole test
    # (StaticPool) so schema + queries see the same DB.
    kwargs = {}
    if TEST_DATABASE_URL.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool
        kwargs = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
    engine = create_async_engine(TEST_DATABASE_URL, **kwargs)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_sessionmaker(db_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(db_sessionmaker):
    async with db_sessionmaker() as s:
        yield s


@pytest_asyncio.fixture
async def patch_global_session(db_sessionmaker, monkeypatch):
    """Redirect app.database.async_session (used directly by many routes/
    services) at the test sessionmaker, so side-effecting handlers hit the
    throwaway DB instead of prod-shaped db:5432."""
    import app.database as dbmod
    monkeypatch.setattr(dbmod, "async_session", db_sessionmaker)
    # Some modules imported `async_session` by name at import time.
    import app.routes.paypal_webhook as pw
    monkeypatch.setattr(pw, "async_session", db_sessionmaker, raising=False)
    return db_sessionmaker
