"""Phase 3 — health + readiness probes."""

import pytest
from starlette.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.integration


def test_liveness_ok():
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_readiness_ok_with_db(patch_global_session):
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/health/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"
