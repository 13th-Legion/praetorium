"""Self-check alerts go to NC Talk, with Discord as a fallback.

Cav, 2026-09-11: alerts belong in NC Talk, not Discord.

Discord is deliberately kept as a *fallback* rather than removed. On
2026-09-11 an unattended glibc/Python upgrade restarted containerd and briefly
broke DNS on the host: every self-check failed at once, and the Talk endpoint
was part of what was down. A Talk-only alert would have failed silently
alongside the thing it was trying to report. An off-box channel is the only
thing that can tell you Nextcloud itself is unreachable.

So the contract these tests pin is:
  * Talk is tried first and, when it works, Discord is NOT used;
  * when Talk fails, Discord IS used, and the message says so.
"""

import pytest

from conftest import FakeResponse


@pytest.fixture
def calls(daemon, monkeypatch):
    """Record Discord sends without touching the network."""
    sent = []

    def _fake_discord(text):
        sent.append(text)
        return True

    monkeypatch.setattr(daemon, "notify_discord", _fake_discord)
    return sent


# ─── happy path: Talk only ────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [200, 201])
def test_talk_success_does_not_touch_discord(daemon, http, calls, status):
    http.always(FakeResponse(status, b'{"ocs":{"meta":{"statuscode":200}}}'))
    assert daemon.notify("hello") is True
    assert calls == [], "Discord must not be used when Talk succeeded"


def test_talk_posts_to_the_ops_room(daemon, http, calls):
    http.always(FakeResponse(201, b"{}"))
    daemon.notify_talk("test message")
    urls = http.urls()
    assert any(f"/chat/{daemon.TALK_ALERT_ROOM}" in u for u in urls), urls
    assert daemon.TALK_ALERT_ROOM == "td853igi", "T2 · Digital Infrastructure"


def test_message_is_sent_as_the_talk_payload(daemon, http, calls):
    http.always(FakeResponse(201, b"{}"))
    daemon.notify_talk("the body text")
    assert any("the body text" in str(c.kwargs) for c in http.calls), http.calls


# ─── fallback: Talk down ──────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [500, 502, 521, 401, 404])
def test_talk_failure_falls_back_to_discord(daemon, http, calls, status):
    http.always(FakeResponse(status, b"nope"))
    assert daemon.notify("alert text") is True
    assert len(calls) == 1, "Discord must be used when Talk failed"
    assert "alert text" in calls[0]


def test_fallback_message_says_it_came_via_discord(daemon, http, calls):
    """So the reader knows Talk was unreachable — itself a signal."""
    http.always(FakeResponse(503, b""))
    daemon.notify("something broke")
    assert "NC Talk was unreachable" in calls[0]


def test_talk_transport_exception_falls_back(daemon, monkeypatch, calls):
    """DNS failure is exactly the 2026-09-11 case."""
    def _boom(*a, **k):
        raise OSError("Temporary failure in name resolution")

    monkeypatch.setattr(daemon, "nc_request", _boom)
    assert daemon.notify("dns is down") is True
    assert len(calls) == 1
    assert "dns is down" in calls[0]


def test_notify_talk_returns_false_when_nc_request_returns_none(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "nc_request", lambda *a, **k: None)
    assert daemon.notify_talk("x") is False
