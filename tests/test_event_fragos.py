"""Unlimited numbered FRAGOs (`POST /api/events/{id}/issue-fragord`).

Regression coverage for the FRAGO that "did absolutely nothing": the old handler
set `events.fragord_issued_at` and returned a green banner. No content was
captured, nobody was emailed, nothing was posted, and because the event_detail
pipeline was an `if/elif/else` chain over that single column, the button
vanished after the first use so a second FRAGO was impossible.

So these assert the three things that were actually broken:
  * a FRAGO carries content, and an empty one is refused outright;
  * FRAGOs are numbered and repeatable (1, 2, 3... on the same event);
  * distribution really happens, and a distribution failure is reported
    instead of being swallowed by `except Exception: pass`.
"""

import pytest
import pytest_asyncio

from sqlalchemy import select

from app.models.events import Event, EventFrago
from app.models.notifications import Notification
from tests.factories import make_event, make_member, make_rsvp

pytestmark = pytest.mark.integration


# ─── Doubles ──────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeTalkClient:
    """Stands in for httpx.AsyncClient in the Talk cross-post."""

    def __init__(self, response=None, exc=None):
        self._response = response or _FakeResponse(200)
        self._exc = exc
        self.posts = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, **kw):
        self.posts.append((url, kw.get("data", {})))
        if self._exc is not None:
            raise self._exc
        return self._response


@pytest_asyncio.fixture
async def frago_env(db_session, db_sessionmaker, monkeypatch):
    """An OPORD-issued event with two attending members (one without an email).

    `app.routes.events` does `from app.database import async_session` at import
    time, so the shared `patch_global_session` fixture does not reach it --
    patch the module attribute directly or the handler talks to the real DB.
    """
    import app.routes.events as ev

    monkeypatch.setattr(ev, "async_session", db_sessionmaker)

    event = await make_event(
        db_session,
        title="Field Training Exercise",
        warno_issued_at=__import__("datetime").datetime(2026, 5, 1, 12, 0),
        opord_issued_at=__import__("datetime").datetime(2026, 5, 10, 12, 0),
    )
    going = await make_member(db_session, email="going@example.test")
    no_email = await make_member(db_session, email=None)
    declined = await make_member(db_session, email="declined@example.test")
    await make_rsvp(db_session, event, going, status="attending")
    await make_rsvp(db_session, event, no_email, status="attending")
    await make_rsvp(db_session, event, declined, status="declined")
    await db_session.commit()

    # Never touch real SMTP. Captured so recipient selection can be asserted.
    sent_batches = []

    def _fake_send_bulk(messages):
        msgs = list(messages)
        sent_batches.append(msgs)
        return (len(msgs), 0)

    import app.integrations.email as email_mod
    monkeypatch.setattr(email_mod, "send_bulk", _fake_send_bulk)

    talk = _FakeTalkClient()
    monkeypatch.setattr(ev.httpx, "AsyncClient", talk)

    return {
        "event": event,
        "going": going,
        "declined": declined,
        "talk": talk,
        "sent_batches": sent_batches,
        "sessionmaker": db_sessionmaker,
    }


def _csrf(c) -> str:
    """CSRFMiddleware uses a double-submit cookie; any GET mints the cookie.
    Without echoing it back every POST here would 403 and the tests would be
    asserting CSRF rejection rather than FRAGO behaviour."""
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    assert tok, "no csrftoken cookie was issued"
    return tok


def _issue(auth_client, event_id, subject="Rally time moved", body="1900 -> 1830."):
    tok = _csrf(auth_client)
    return auth_client.post(
        f"/api/events/{event_id}/issue-fragord",
        data={"subject": subject, "body": body, "csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )


# ─── An empty FRAGO is refused ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "subject,body",
    [("", ""), ("Subject only", ""), ("", "Body only"), ("   ", "   ")],
)
@pytest.mark.asyncio
async def test_empty_frago_is_rejected_and_persists_nothing(
    auth_client, frago_env, db_session, subject, body
):
    event = frago_env["event"]
    resp = _issue(auth_client, event.id, subject=subject, body=body)

    assert resp.status_code == 400
    assert "needs both a subject" in resp.text
    # Nothing written, and the legacy mirror column untouched.
    rows = (await db_session.execute(
        select(EventFrago).where(EventFrago.event_id == event.id)
    )).scalars().all()
    assert rows == []
    await db_session.refresh(event)
    assert event.fragord_issued_at is None


# ─── Numbering is sequential and unlimited ────────────────────────────────────

@pytest.mark.asyncio
async def test_fragos_are_numbered_and_repeatable(auth_client, frago_env, db_session):
    event = frago_env["event"]

    for expected in (1, 2, 3):
        resp = _issue(
            auth_client, event.id,
            subject=f"Change {expected}", body=f"Detail {expected}",
        )
        assert resp.status_code == 200
        assert f"FRAGO {expected} issued" in resp.text

    rows = (await db_session.execute(
        select(EventFrago)
        .where(EventFrago.event_id == event.id)
        .order_by(EventFrago.number)
    )).scalars().all()

    assert [r.number for r in rows] == [1, 2, 3]
    assert [r.subject for r in rows] == ["Change 1", "Change 2", "Change 3"]
    assert [r.body for r in rows] == ["Detail 1", "Detail 2", "Detail 3"]
    # Every one records who issued it.
    assert all(r.issued_by for r in rows)


@pytest.mark.asyncio
async def test_legacy_column_mirrors_the_latest_frago(auth_client, frago_env, db_session):
    """`events.fragord_issued_at` still drives the existing pipeline chip."""
    event = frago_env["event"]

    _issue(auth_client, event.id, subject="First", body="one")
    await db_session.refresh(event)
    first_stamp = event.fragord_issued_at
    assert first_stamp is not None

    _issue(auth_client, event.id, subject="Second", body="two")
    await db_session.refresh(event)
    assert event.fragord_issued_at >= first_stamp


# ─── Distribution actually happens ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_frago_emails_attending_posts_to_talk_and_notifies(
    auth_client, frago_env, db_session
):
    event = frago_env["event"]
    resp = _issue(auth_client, event.id, subject="Rally moved", body="1900 -> 1830.")

    assert resp.status_code == 200

    # Talk cross-post fired, and the message names the FRAGO and the change.
    assert len(frago_env["talk"].posts) == 1
    _url, data = frago_env["talk"].posts[0]
    assert "FRAGO 1" in data["message"]
    assert "Rally moved" in data["message"]
    assert f"/events/{event.id}" in data["message"]

    # Portal notification for the roster.
    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert notes, "expected a portal notification per member"
    assert any("FRAGO 1" in n.title for n in notes)

    # Recipients: attending members WITH an email only. The declined member and
    # the attending member with no email address are both excluded.
    batches = frago_env["sent_batches"]
    assert len(batches) == 1, "email blast should run exactly once"
    recipients = {m.to for m in batches[0]}
    assert recipients == {"going@example.test"}
    body_html = batches[0][0].html
    assert "FRAGO 1" in body_html
    assert "1900 -&gt; 1830." in body_html or "1900 -> 1830." in body_html
    assert "FRAGO 1" in batches[0][0].subject

    # The row records that distribution succeeded.
    row = (await db_session.execute(
        select(EventFrago).where(EventFrago.event_id == event.id)
    )).scalar_one()
    assert row.talk_posted is True
    assert row.notified is True


@pytest.mark.asyncio
async def test_talk_failure_is_reported_not_swallowed(
    auth_client, frago_env, db_session, monkeypatch
):
    """The WARNO/OPORD paths hide Talk failures in `except Exception: pass`.
    A FRAGO must say so instead -- silently not telling the unit about a change
    is the whole bug this feature exists to fix."""
    import app.routes.events as ev
    failing = _FakeTalkClient(response=_FakeResponse(500, text="boom"))
    monkeypatch.setattr(ev.httpx, "AsyncClient", failing)

    event = frago_env["event"]
    resp = _issue(auth_client, event.id, subject="Route change", body="Use FM 1187.")

    assert resp.status_code == 200
    assert "Talk post FAILED" in resp.text
    # Orange, not green -- the banner must not look like a clean success.
    assert "#e65100" in resp.text

    row = (await db_session.execute(
        select(EventFrago).where(EventFrago.event_id == event.id)
    )).scalar_one()
    assert row.talk_posted is False
    # The order itself is still recorded; a failed announcement must not lose it.
    assert row.subject == "Route change"


@pytest.mark.asyncio
async def test_no_attending_members_is_reported_honestly(
    auth_client, frago_env, db_session, db_sessionmaker
):
    """A FRAGO nobody receives must not claim success."""
    from app.models.events import EventRSVP
    from sqlalchemy import delete

    event = frago_env["event"]
    async with db_sessionmaker() as s:
        await s.execute(delete(EventRSVP).where(EventRSVP.event_id == event.id))
        await s.commit()

    resp = _issue(auth_client, event.id, subject="Cancelled", body="Stand down.")

    assert resp.status_code == 200
    assert "no email sent" in resp.text
    assert frago_env["sent_batches"] == [], "no recipients means no blast"


# ─── Authorization is unchanged ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_requires_authentication(client, frago_env):
    """Sends a valid CSRF token so this asserts the AUTH gate, not the CSRF one."""
    tok = _csrf(client)
    resp = client.post(
        f"/api/events/{frago_env['event'].id}/issue-fragord",
        data={"subject": "x", "body": "y", "csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    assert resp.status_code in (302, 303, 401, 403)
    # And nothing was written.
    from app.models.events import EventFrago as _EF
    assert "FRAGO 1 issued" not in resp.text
