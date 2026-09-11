"""Unit email policy, and the member-edit save path that enforces it.

Policy (Cav, 2026-09-10):
  * official address (members.email, and the Nextcloud account address kept in
    step with it) MUST be Proton — nothing else;
  * personal_email may be anything EXCEPT their own official Proton address.

The integration test at the bottom exists for a specific reason: the
member-edit save handler had a `NameError` (`_old_email` referenced but never
assigned, after a partially-applied edit) that shipped to production. It passed
py_compile and the entire suite because **nothing exercised that handler**.
Syntax checks cannot catch an unbound local; only running the code can.
"""

import pytest

from sqlalchemy import select

from app.models.member import Member
from app.services import email_policy
from tests.factories import make_member

unit = pytest.mark.unit


# ─── is_proton ────────────────────────────────────────────────────────────────

@unit
@pytest.mark.parametrize("addr", [
    "someone@proton.me",
    "someone@protonmail.com",
    "Mixed.Case@Proton.Me",
    "  padded@proton.me  ",
])
def test_proton_addresses_accepted(addr):
    assert email_policy.is_proton(addr) is True


@unit
@pytest.mark.parametrize("addr", [
    "someone@gmail.com",
    "kyleconrey@yahoo.com",
    "adam.locy@13thlegion.org",      # unit domain is still not Proton
    "someone@proton.me.evil.com",    # suffix must be the actual domain
    "someone@notproton.me",
    "", None,
])
def test_non_proton_rejected(addr):
    assert email_policy.is_proton(addr) is False


# ─── validate_official ────────────────────────────────────────────────────────

@unit
def test_official_accepts_proton():
    ok, why = email_policy.validate_official("romanovtsm@proton.me")
    assert ok is True and why == ""


@unit
@pytest.mark.parametrize("addr,fragment", [
    ("", "required"),
    (None, "required"),
    ("notanemail", "not a valid"),
    ("kyleconrey@yahoo.com", "must be a Proton address"),
    ("adam.locy@13thlegion.org", "must be a Proton address"),
])
def test_official_rejects(addr, fragment):
    ok, why = email_policy.validate_official(addr)
    assert ok is False
    assert fragment in why


# ─── validate_personal ────────────────────────────────────────────────────────

@unit
@pytest.mark.parametrize("personal", ["", None])
def test_personal_is_optional(personal):
    ok, _ = email_policy.validate_personal(personal, "x@proton.me")
    assert ok is True


@unit
def test_personal_may_be_non_proton():
    ok, _ = email_policy.validate_personal("lkavadas@gmail.com", "x@proton.me")
    assert ok is True


@unit
def test_personal_may_be_a_different_proton_address():
    """'anything but THEIR proton address' — a second Proton mailbox is fine."""
    ok, _ = email_policy.validate_personal("other@proton.me", "official@proton.me")
    assert ok is True


@unit
@pytest.mark.parametrize("personal,official", [
    ("avpg17@proton.me", "avpg17@proton.me"),
    ("Romanovtsm@proton.me", "romanovtsm@proton.me"),   # case-insensitive
    ("  padded@proton.me ", "padded@proton.me"),
])
def test_personal_may_not_duplicate_official(personal, official):
    ok, why = email_policy.validate_personal(personal, official)
    assert ok is False
    assert "different from your official" in why


# ─── the save path actually runs ──────────────────────────────────────────────

def _csrf(c):
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    return tok


def _form(**over):
    base = {
        "first_name": "Test", "last_name": "Member", "callsign": "Tester",
        "email": "tester@proton.me", "rank_grade": "E-4", "status": "active",
    }
    base.update(over)
    return base


@pytest.mark.integration
@pytest.mark.asyncio
async def test_member_edit_save_runs_end_to_end(
    auth_client, db_session, db_sessionmaker, patch_global_session, monkeypatch
):
    """Regression: this handler shipped with an unbound local and 500'd on every
    save. A test that merely imports the module cannot catch that."""
    import app.routes.member_edit as me

    pushed = []

    async def _fake_set_email(username, email, **kw):
        pushed.append((username, email))
        return True, "ok"

    monkeypatch.setattr(me.nc_users, "set_email", _fake_set_email)
    # Neutralise the other Nextcloud side-effects this handler fires.
    for name in ("_sync_nc_displayname", "_sync_leadership_groups", "_sync_shop_groups"):
        if hasattr(me, name):
            async def _noop(*a, **k):
                return None
            monkeypatch.setattr(me, name, _noop)

    m = await make_member(db_session, nc_username="tester",
                          email="old@proton.me", first_name="Test",
                          last_name="Member")
    await db_session.commit()

    tok = _csrf(auth_client)
    resp = auth_client.post(
        f"/api/members/{m.id}/edit",
        data={**_form(email="new@proton.me"), "csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )

    assert resp.status_code == 303, resp.text[:500]

    async with db_sessionmaker() as s:
        saved = (await s.execute(select(Member).where(Member.id == m.id))).scalar_one()
        assert saved.email == "new@proton.me"

    assert pushed == [("tester", "new@proton.me")], \
        "the changed address must be pushed to Nextcloud"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_member_edit_rejects_non_proton_official(
    auth_client, db_session, db_sessionmaker, patch_global_session, monkeypatch
):
    import app.routes.member_edit as me

    async def _fail(*a, **k):
        raise AssertionError("must not reach Nextcloud on a rejected save")

    monkeypatch.setattr(me.nc_users, "set_email", _fail)

    m = await make_member(db_session, nc_username="tester2",
                          email="keep@proton.me", first_name="Test",
                          last_name="Member")
    await db_session.commit()

    tok = _csrf(auth_client)
    resp = auth_client.post(
        f"/api/members/{m.id}/edit",
        data={**_form(email="someone@gmail.com"), "csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )

    assert resp.status_code == 400
    assert "Proton" in resp.text

    async with db_sessionmaker() as s:
        saved = (await s.execute(select(Member).where(Member.id == m.id))).scalar_one()
        assert saved.email == "keep@proton.me", "a rejected save must not persist"
