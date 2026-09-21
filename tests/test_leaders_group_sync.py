"""Leaders group sync: rank counts, and unrelated edits must not evict anyone.

Reported 2026-09-17: SGT Gonzalez was removed from T1 - Leaders by the portal
service account. Cav had edited his profile to tick the "founder" box.

The founder flag was the trigger, not the cause. `_sync_leadership_groups` runs
on every profile save and recomputed Leaders membership from six hardcoded
leadership titles plus a '(Lead)' billet. Gonzalez is an E-5 with no leadership
title and no '(Lead)' billet, so he failed the check and was deleted from the
group — and T1 - Leaders has the `Leaders` group attached, so he lost the chat.

Two fixes, both pinned here:
  1. Rank counts. The room is labelled "Leaders (NCOs + Officers)" and NC
     already carries Rank - NCO / Rank - Officer groups.
  2. Removal only happens when the edit actually touched rank, title or
     billets. Saving a phone number must never evict anyone.
"""
import pytest

from app.routes.member_edit import _is_leader_rank, _sync_leadership_groups


# ─── rank predicate ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("rank,expected", [
    ("E-5", True),    # Gonzalez — the reported case
    ("E-6", True),
    ("E-7", True),
    ("E-8", True),
    ("E-9", True),
    ("O-1", True),    # Wellman — Platoon Leader, not in the title allowlist
    ("O-3", True),
    ("W-2", True),
    ("E-4", False),   # not an NCO
    ("E-1", False),
    (None, False),
    ("", False),
    ("bogus", False),
])
def test_is_leader_rank(rank, expected):
    assert _is_leader_rank(rank) is expected


def test_rank_parsing_tolerates_formatting():
    """Ranks are stored hyphenated ('E-5'). Matching on 'E5' matched nothing
    and understated the blast radius when this was first investigated."""
    assert _is_leader_rank("E5") is True
    assert _is_leader_rank("e-5") is True
    assert _is_leader_rank(" E-5 ") is True


# ─── sync behaviour ──────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, groups):
        self._groups = groups

    def raise_for_status(self):
        return None

    def json(self):
        return {"ocs": {"data": {"groups": self._groups}}}


class _FakeClient:
    """Records group add/remove calls instead of hitting Nextcloud."""

    def __init__(self, current_groups):
        self.current_groups = current_groups
        self.added = []
        self.removed = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        return _FakeResp(self.current_groups)

    async def post(self, url, **kw):
        self.added.append((kw.get("data") or {}).get("groupid"))
        return _FakeResp(self.current_groups)

    async def request(self, method, url, **kw):
        if method == "DELETE":
            self.removed.append((kw.get("data") or {}).get("groupid"))
        return _FakeResp(self.current_groups)


@pytest.fixture
def fake_client(monkeypatch):
    import app.routes.member_edit as me

    def _make(groups):
        c = _FakeClient(groups)
        monkeypatch.setattr(me.httpx, "AsyncClient", c)
        return c
    return _make


@pytest.mark.asyncio
async def test_nco_without_title_is_not_evicted(fake_client):
    """The exact Gonzalez case: E-5, no leadership title, no '(Lead)' billet."""
    c = fake_client(["13th Legion", "Leaders", "Rank - NCO", "Team-Foxtrot"])
    await _sync_leadership_groups(
        "ramiro.gonzalez", None, "Foxtrot",
        billets="S1: Administration, S2: Intel & Security",
        rank_grade="E-5",
    )
    assert "Leaders" not in c.removed, "an E-5 NCO must keep Leaders"


@pytest.mark.asyncio
async def test_nco_missing_from_leaders_gets_added(fake_client):
    c = fake_client(["13th Legion", "Rank - NCO", "Team-Foxtrot"])
    await _sync_leadership_groups(
        "ramiro.gonzalez", None, "Foxtrot", billets=None, rank_grade="E-5",
    )
    assert "Leaders" in c.added


@pytest.mark.asyncio
async def test_platoon_leader_officer_is_not_evicted(fake_client):
    """'Platoon Leader' is NOT in the hardcoded title allowlist (that has
    'Platoon Sergeant, Training NCO'), so Wellman survived only by luck until
    his next profile save."""
    c = fake_client(["13th Legion", "Command", "Leaders", "Rank - Officer"])
    await _sync_leadership_groups(
        "matt.wellman", "Platoon Leader", "Foxtrot", billets=None, rank_grade="O-1",
    )
    assert "Leaders" not in c.removed


@pytest.mark.asyncio
async def test_junior_enlisted_still_removed_when_inputs_changed(fake_client):
    """The sync must still work: an E-4 with no title does not belong."""
    c = fake_client(["13th Legion", "Leaders", "Team-Foxtrot"])
    await _sync_leadership_groups(
        "paul.cyr", None, "Foxtrot", billets=None, rank_grade="E-4",
        allow_remove=True,
    )
    assert "Leaders" in c.removed


@pytest.mark.asyncio
async def test_unrelated_edit_never_removes(fake_client):
    """The founder-checkbox scenario: an edit that touched nothing relevant
    must not evict, even for someone who genuinely fails the check."""
    c = fake_client(["13th Legion", "Leaders", "Team-Foxtrot"])
    await _sync_leadership_groups(
        "paul.cyr", None, "Foxtrot", billets=None, rank_grade="E-4",
        allow_remove=False,
    )
    assert c.removed == [], "an unrelated edit must never remove Leaders"


@pytest.mark.asyncio
async def test_lead_billet_still_counts(fake_client):
    """Pre-existing behaviour preserved: a '(Lead)' billet grants Leaders even
    below E-5."""
    c = fake_client(["13th Legion", "Team-Aquila"])
    await _sync_leadership_groups(
        "someone.junior", None, "Aquila",
        billets="S1: Administration (Lead)", rank_grade="E-4",
    )
    assert "Leaders" in c.added
