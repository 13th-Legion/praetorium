"""S1 at-a-glance stat strip (`GET /api/s1/glance`).

Regression coverage for the 500 that hit every load of the S1 dashboard:
`patched` and `active` were computed only inside the `except` handler wrapping
the Deck fetch, so the success path (Deck reachable — the normal case) never
bound them and the "Active members" card raised UnboundLocalError. The except
branch was broken too: its queries used the `db` session from an `async with`
block that had already exited.

So the counts are asserted on BOTH sides of the Deck call, not just one.
"""

import pytest

from tests.factories import make_member

pytestmark = pytest.mark.integration


# ─── Deck API doubles ─────────────────────────────────────────────────────────
#
# s1_glance does `async with httpx.AsyncClient(...) as client: await client.get(...)`,
# so the double needs to be an async context manager exposing `get`.

class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    """Returns a canned response, or raises, on .get()."""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    def __call__(self, *a, **kw):      # stands in for httpx.AsyncClient(...)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, *a, **kw):
        if self._exc is not None:
            raise self._exc
        return self._response


# DECK_ACTIVE_STACKS = {11,12,13,14,15}; ON_HOLD_STACK = 81.
# 2 + 1 live applicants across active stacks, 1 archived card that must NOT be
# counted, and 3 on hold reported separately.
DECK_STACKS = [
    {"id": 11, "cards": [{"id": 1}, {"id": 2}]},
    {"id": 12, "cards": [{"id": 3}, {"id": 4, "archived": True}]},
    {"id": 16, "cards": [{"id": 5}, {"id": 6}]},          # Complete → on roster, excluded
    {"id": 81, "cards": [{"id": 7}, {"id": 8}, {"id": 9}]},
]


def _patch_deck(monkeypatch, *, response=None, exc=None):
    import app.routes.s1_admin as s1
    monkeypatch.setattr(s1.httpx, "AsyncClient", _FakeClient(response=response, exc=exc))


async def _seed_roster(db):
    """3 patched + 2 recruits currently serving; 2 not on the default roster.

    Distinct numbers per bucket so a wrong predicate can't accidentally pass.
    """
    for _ in range(3):
        await make_member(db, status="active")
    for _ in range(2):
        await make_member(db, status="recruit")
    await make_member(db, status="inactive")
    await make_member(db, status="separated")
    await db.commit()


class TestS1Glance:
    async def test_success_path_renders_all_three_cards(
        self, auth_client, patch_global_session, db_session, monkeypatch
    ):
        """Deck reachable — the path that used to 500 with UnboundLocalError."""
        await _seed_roster(db_session)
        _patch_deck(monkeypatch, response=_FakeResponse(200, DECK_STACKS))

        r = auth_client.get("/api/s1/glance")

        assert r.status_code == 200, r.text
        body = r.text
        assert "Pending training claims" in body
        assert "Applicants" in body
        assert "Active members" in body
        # active = status.in_(["active","recruit"]) = 5, matching roster_list.
        assert ">5<" in body
        # patched = status == "active" = 3; recruits = 2.
        assert "3 patched · 2 recruits" in body
        # 2 (stack 11) + 1 live (stack 12, archived excluded) = 3 applicants.
        assert ">3<" in body
        assert "3 on hold" in body
        assert "Deck unreachable" not in body

    async def test_deck_unreachable_still_returns_200_with_counts(
        self, auth_client, patch_global_session, db_session, monkeypatch
    ):
        """Except path — its DB queries used to run on a closed session."""
        await _seed_roster(db_session)
        _patch_deck(monkeypatch, exc=RuntimeError("connect timeout"))

        r = auth_client.get("/api/s1/glance")

        assert r.status_code == 200, r.text
        body = r.text
        assert "Deck unreachable" in body
        assert "\u2014" in body                      # em dash, not a fake 0
        # Roster counts must survive the Deck failure.
        assert ">5<" in body
        assert "3 patched · 2 recruits" in body

    async def test_deck_non_200_reports_honestly(
        self, auth_client, patch_global_session, db_session, monkeypatch
    ):
        await _seed_roster(db_session)
        _patch_deck(monkeypatch, response=_FakeResponse(503, None, text="upstream down"))

        r = auth_client.get("/api/s1/glance")

        assert r.status_code == 200, r.text
        assert "Deck unreachable" in r.text
        assert "3 patched · 2 recruits" in r.text

    async def test_empty_roster_renders_zeros(
        self, auth_client, patch_global_session, db_session, monkeypatch
    ):
        """No members at all: `or 0` fallbacks must not leave a name unbound."""
        _patch_deck(monkeypatch, response=_FakeResponse(200, []))

        r = auth_client.get("/api/s1/glance")

        assert r.status_code == 200, r.text
        assert "0 patched · 0 recruits" in r.text
