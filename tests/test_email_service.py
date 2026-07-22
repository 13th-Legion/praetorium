"""Shared email service (app/integrations/email.py) — SMTP mocked."""

from unittest import mock

import pytest

from app.integrations import email as email_service

pytestmark = pytest.mark.unit


class _FakeSMTP:
    """Records sendmail calls; no network."""
    instances = []

    def __init__(self, *a, **k):
        self.sent = []
        self.logged_in = False
        _FakeSMTP.instances.append(self)

    def starttls(self): pass
    def login(self, u, p): self.logged_in = True
    def sendmail(self, frm, to, body): self.sent.append((frm, to, body))
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _build_mime_has(m, needle):
    return needle in email_service._build_mime(m).as_string()


def test_send_email_success():
    _FakeSMTP.instances.clear()
    with mock.patch("app.integrations.email.smtplib.SMTP", _FakeSMTP):
        ok = email_service.send_email("x@y.z", "Subj", "<b>hi</b>", text="hi")
    assert ok is True
    assert _FakeSMTP.instances[-1].sent  # one message sent


def test_send_email_failure_returns_false():
    def boom(*a, **k):
        raise OSError("smtp down")
    with mock.patch("app.integrations.email.smtplib.SMTP", boom):
        assert email_service.send_email("x@y.z", "S", "<b>h</b>") is False


def test_send_bulk_reuses_one_connection():
    _FakeSMTP.instances.clear()
    msgs = [email_service.EmailMessage(to=f"m{i}@x.z", subject="S", html="<b>h</b>")
            for i in range(5)]
    with mock.patch("app.integrations.email.smtplib.SMTP", _FakeSMTP):
        sent, failed = email_service.send_bulk(msgs)
    assert (sent, failed) == (5, 0)
    # exactly ONE SMTP connection opened for all 5
    assert len(_FakeSMTP.instances) == 1
    assert len(_FakeSMTP.instances[0].sent) == 5


def test_send_bulk_empty():
    assert email_service.send_bulk([]) == (0, 0)


def test_mime_has_html_and_subject():
    m = email_service.EmailMessage(to="a@b.c", subject="Hello", html="<p>x</p>", text="x")
    s = email_service._build_mime(m).as_string()
    assert "Hello" in s and "text/html" in s and "text/plain" in s
