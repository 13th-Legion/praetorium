"""Publishing an AAR or OPORD twice must not re-notify the unit.

Reported 2026-09-16: SGT Deaton published an FTX AAR and every member got the
notification twice. The publish handler set `events.aar_published_at`
unconditionally — there was no check that it was already set — so a second
submit re-ran the whole of `_aar_bg()`: an email to every active member and
recruit, a Talk cross-post, and one notification per member.

Production evidence: two fan-outs ~6s apart, 125 members x 2 = 250 notification
rows, and two identical Talk posts in T1 Announcements (comments 50213/50214).
The button already carried `hx-disabled-elt`, so client-side guarding was not
enough — the response returns immediately (the fan-out is backgrounded), the
button re-enables, the page reloads, and the re-rendered form offers Publish
again.

`issue_opord` had the identical hole and the identical three-way fan-out, so it
is covered here too.
"""

import asyncio
import datetime

import pytest
import pytest_asyncio

from app.models.events import Event

from tests.factories import make_event, make_member, make_rsvp

pytestmark = pytest.mark.integration


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeTalkClient:
    """Stands in for httpx.AsyncClient in the Talk cross-post."""

    def __init__(self):
        self.posts = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, **kw):
        self.posts.append((url, kw.get("data", {})))
        return _FakeResponse(200)


async def _drain():
    """Let fire-and-forget `asyncio.ensure_future(_aar_bg())` work run.

    The fan-out is deliberately backgrounded, so without draining the test
    would pass even if the second publish HAD re-notified.
    """
    for _ in range(12):
        await asyncio.sleep(0.02)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending:
            break


@pytest_asyncio.fixture
async def pub_env(db_session, db_sessionmaker, patch_global_session, monkeypatch):
    """A finalized FTX with attending members, and all distribution stubbed."""
    import app.routes.events as ev

    event = await make_event(
        db_session,
        title="Field Training Exercise",
        warno_issued_at=datetime.datetime(2026, 9, 1, 12, 0),
        finalized_at=datetime.datetime(2026, 9, 14, 12, 0),
    )
    m1 = await make_member(db_session, email="one@example.test")
    m2 = await make_member(db_session, email="two@example.test")
    await make_rsvp(db_session, event, m1, status="attending")
    await make_rsvp(db_session, event, m2, status="attending")
    await db_session.commit()

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
        # Plain int: after expire_all(), touching event.id would trigger a lazy
        # reload from sync context and raise MissingGreenlet.
        "event_id": int(event.id),
        "talk": talk,
        "sent_batches": sent_batches,
    }


def _csrf(c) -> str:
    tok = c.cookies.get("csrftoken")
    if not tok:
        c.get("/health")
        tok = c.cookies.get("csrftoken")
    assert tok, "no csrftoken cookie was issued"
    return tok


def _publish_aar(client, event_id, action="publish", right_1="Comms held up"):
    tok = _csrf(client)
    return client.post(
        f"/api/events/{event_id}/aar",
        data={
            "action": action,
            "commander_intent": "Rehearse dismounted movement.",
            "mission_summary": "Two days, no casualties.",
            "right_1": right_1,
            "csrf_token": tok,
        },
        headers={"X-CSRF-Token": tok},
    )


# ─── AAR ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_second_aar_publish_does_not_refan_out(auth_client, pub_env, db_session):
    """The actual reported bug: a second publish must not notify anyone again."""
    first = _publish_aar(auth_client, pub_env["event_id"])
    assert first.status_code == 200, first.text
    assert "published" in first.text.lower()
    await _drain()

    talk_after_first = len(pub_env["talk"].posts)
    emails_after_first = len(pub_env["sent_batches"])
    assert talk_after_first == 1, pub_env["talk"].posts
    assert emails_after_first == 1

    second = _publish_aar(auth_client, pub_env["event_id"], right_1="Comms held up, mostly")
    assert second.status_code == 200, second.text
    await _drain()

    # No second Talk post, no second email batch.
    assert len(pub_env["talk"].posts) == talk_after_first, (
        "second publish re-posted to Talk — this is the 50213/50214 duplicate"
    )
    assert len(pub_env["sent_batches"]) == emails_after_first, (
        "second publish re-emailed the unit"
    )


@pytest.mark.asyncio
async def test_second_aar_publish_reports_already_published(auth_client, pub_env):
    _publish_aar(auth_client, pub_env["event_id"])
    await _drain()
    second = _publish_aar(auth_client, pub_env["event_id"])
    body = second.text.lower()
    assert "already published" in body, second.text
    assert "not emailed" in body or "not" in body


@pytest.mark.asyncio
async def test_second_aar_publish_still_saves_edits(auth_client, pub_env, db_session):
    """Editing a published AAR is legitimate — it just must not re-notify."""
    eid = pub_env["event_id"]
    _publish_aar(auth_client, eid)
    await _drain()
    _publish_aar(auth_client, eid, right_1="Revised finding")
    await _drain()

    db_session.expire_all()
    ev = await db_session.get(Event, eid)
    assert ev.aar_mission_summary == "Two days, no casualties."


@pytest.mark.asyncio
async def test_aar_published_timestamp_is_not_overwritten(auth_client, pub_env, db_session):
    """The original publish time is the record; a re-save must not move it."""
    eid = pub_env["event_id"]
    _publish_aar(auth_client, eid)
    await _drain()

    db_session.expire_all()
    ev = await db_session.get(Event, eid)
    first_at = ev.aar_published_at
    assert first_at is not None

    _publish_aar(auth_client, eid)
    await _drain()

    db_session.expire_all()
    ev = await db_session.get(Event, eid)
    assert ev.aar_published_at == first_at


# ─── OPORD ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_second_opord_issue_is_refused(auth_client, pub_env, db_session):
    """Same hole, same fan-out: issue-opord had no guard either."""
    tok = _csrf(auth_client)
    eid = pub_env["event_id"]

    first = auth_client.post(
        f"/api/events/{eid}/issue-opord",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    assert first.status_code == 200, first.text
    await _drain()
    talk_after_first = len(pub_env["talk"].posts)

    second = auth_client.post(
        f"/api/events/{eid}/issue-opord",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    assert second.status_code == 200, second.text
    assert "already issued" in second.text.lower(), second.text
    await _drain()

    assert len(pub_env["talk"].posts) == talk_after_first, (
        "second issue-opord re-posted to Talk"
    )


@pytest.mark.asyncio
async def test_opord_issued_timestamp_is_not_overwritten(auth_client, pub_env, db_session):
    tok = _csrf(auth_client)
    eid = pub_env["event_id"]
    auth_client.post(
        f"/api/events/{eid}/issue-opord",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    await _drain()
    db_session.expire_all()
    ev = await db_session.get(Event, eid)
    first_at = ev.opord_issued_at
    assert first_at is not None

    second = auth_client.post(
        f"/api/events/{eid}/issue-opord",
        data={"csrf_token": tok},
        headers={"X-CSRF-Token": tok},
    )
    # Guard against a vacuous pass: the timestamp would also be unchanged if
    # this request had simply 404'd.
    assert second.status_code == 200, second.text
    assert "already issued" in second.text.lower(), second.text
    await _drain()
    db_session.expire_all()
    ev = await db_session.get(Event, eid)
    assert ev.opord_issued_at == first_at
