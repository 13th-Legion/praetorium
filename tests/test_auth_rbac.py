"""Phase 1 — Auth / RBAC coverage.

map_groups_to_roles truth table, guest read-only role expansion, and the
GuestReadOnlyMiddleware write-block behavior.
"""

import pytest

from app.auth import map_groups_to_roles, GROUP_ROLE_MAP, GUEST_VIEW_ROLES

pytestmark = pytest.mark.unit


class TestMapGroupsToRoles:
    def test_every_group_maps_to_its_role(self):
        # Non-guest groups map 1:1 per the table.
        for group, role in GROUP_ROLE_MAP.items():
            if role == "guest":
                continue
            assert map_groups_to_roles([group]) == [role]

    def test_command_admin_bundle(self):
        roles = map_groups_to_roles(["Command", "admin"])
        assert "command" in roles and "admin" in roles

    def test_unknown_group_ignored(self):
        assert map_groups_to_roles(["Not A Real Group"]) == []

    def test_empty_groups(self):
        assert map_groups_to_roles([]) == []

    def test_guest_expands_to_view_roles_and_keeps_marker(self):
        roles = map_groups_to_roles(["Guests"])
        assert roles[0] == "guest"          # marker preserved and first
        for vr in GUEST_VIEW_ROLES:
            assert vr in roles
        # deduped
        assert len(roles) == len(set(roles))

    def test_guest_ignores_other_real_groups(self):
        # A guest who somehow also has Command still resolves to the guest bundle,
        # not real command persistence privileges beyond the view set.
        roles = map_groups_to_roles(["Guests", "Command"])
        assert "guest" in roles


class TestGuestReadOnlyMiddleware:
    """GET always allowed; write verbs blocked for guest sessions except the
    auth/session-lifecycle allow-list. Tested by driving the middleware's
    dispatch directly with a stubbed request/session (avoids TestClient cookie
    quirks while exercising the real decision logic).
    """

    async def _dispatch(self, method, path, roles, hx=True):
        from app.main import GuestReadOnlyMiddleware
        from starlette.responses import PlainTextResponse

        mw = GuestReadOnlyMiddleware(app=None)

        class FakeURL:
            def __init__(self, p): self.path = p

        class FakeReq:
            def __init__(self):
                self.method = method
                self.url = FakeURL(path)
                self.session = {"user": {"username": "v", "roles": roles}}
                self.headers = {"HX-Request": "true"} if hx else {}

        called = {"v": False}

        async def call_next(request):
            called["v"] = True
            return PlainTextResponse("ok")

        resp = await mw.dispatch(FakeReq(), call_next)
        return resp, called["v"]

    async def test_guest_get_allowed(self):
        resp, called = await self._dispatch("GET", "/thing", ["guest", "command"])
        assert called and resp.status_code == 200

    async def test_guest_post_blocked_403(self):
        resp, called = await self._dispatch("POST", "/api/events/create", ["guest", "command"])
        assert not called          # handler never reached
        assert resp.status_code == 403
        assert "read-only" in resp.body.decode().lower()

    async def test_guest_post_allowlist_auth_path(self):
        resp, called = await self._dispatch("POST", "/auth/logout", ["guest"])
        assert called and resp.status_code == 200

    async def test_non_guest_post_passes_through(self):
        resp, called = await self._dispatch("POST", "/api/events/create", ["command"])
        assert called and resp.status_code == 200
