"""Proton address validation + welcome-email failure handling.

Two related guards against the same failure class: a recruit whose Proton
address does not exist. The welcome email 554-bounces and, historically, the
daemon logged the error but still marked onboarding complete — leaving a
recruit with an NC account + portal row but no credentials and no way in.

1. `is_valid_proton_email` rejects obviously-wrong values before any account
   is created.
2. `send_welcome_email` distinguishes permanent (5xx) from transient (4xx)
   failure so the caller can halt instead of silently completing.
"""

import smtplib

import pytest


# ─── is_valid_proton_email (pure) ────────────────────────────────────────────

@pytest.mark.parametrize("addr", [
    "recruit@proton.me",
    "recruit.name@protonmail.com",
    "recruit+tag@protonmail.ch",
    "recruit@pm.me",
])
def test_valid_proton_addresses(daemon, addr):
    assert daemon.is_valid_proton_email(addr) is True


@pytest.mark.parametrize("addr", [
    "not-an-email",
    "recruit@gmail.com",            # non-Proton
    "recruit@example.com",          # non-Proton
    "bahoojawooja@protonmail.com",  # well-formed but does not exist — SMTP's job
    "",
    None,
])
def test_invalid_proton_addresses(daemon, addr):
    # The "does not exist" address is well-formed and therefore passes the
    # syntax check — that is intentional; only the SMTP bounce can catch it.
    if addr == "bahoojawooja@protonmail.com":
        assert daemon.is_valid_proton_email(addr) is True
        return
    assert daemon.is_valid_proton_email(addr) is False


def test_whitespace_is_stripped(daemon):
    assert daemon.is_valid_proton_email("  recruit@proton.me  ") is True


# ─── send_welcome_email failure classification ───────────────────────────────

class _RecipientsRefused(smtplib.SMTPRecipientsRefused):
    def __init__(self):
        super().__init__({"bad@x": (554, b"does not exist")})


class _Permanent(smtplib.SMTPResponseException):
    def __init__(self):
        super().__init__(554, b"does not exist")


class _Transient(smtplib.SMTPResponseException):
    def __init__(self):
        super().__init__(451, b"temporary failure")


@pytest.fixture
def smtp_ok(daemon, monkeypatch):
    class _S:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self):
            pass
        def login(self, *a, **k):
            pass
        def send_message(self, *a, **k):
            pass
    monkeypatch.setattr(daemon.smtplib, "SMTP", lambda *a, **k: _S())
    return _S


def test_welcome_email_success(daemon, smtp_ok):
    assert daemon.send_welcome_email("r@proton.me", "Jane Doe", "jane.doe", "pw", "Alpha") is True


def test_welcome_email_recipients_refused_is_permanent(daemon, monkeypatch):
    def boom(*a, **k):
        raise _RecipientsRefused()
    monkeypatch.setattr(daemon.smtplib, "SMTP", boom)
    assert daemon.send_welcome_email("r@proton.me", "J", "j", "pw", "A") == "permanent"


def test_welcome_email_5xx_is_permanent(daemon, monkeypatch):
    def boom(*a, **k):
        raise _Permanent()
    monkeypatch.setattr(daemon.smtplib, "SMTP", boom)
    assert daemon.send_welcome_email("r@proton.me", "J", "j", "pw", "A") == "permanent"


def test_welcome_email_4xx_is_transient(daemon, monkeypatch):
    def boom(*a, **k):
        raise _Transient()
    monkeypatch.setattr(daemon.smtplib, "SMTP", boom)
    assert daemon.send_welcome_email("r@proton.me", "J", "j", "pw", "A") is False


# ─── Training calendar entry (pure) ─────────────────────────────────────────

from datetime import datetime as _dt


def _d(y, m, dd):
    return _dt(y, m, dd)


NOW = _dt(2026, 10, 19)  # reference "today"


def test_calendar_past_ftx_is_struck(daemon):
    label, past = daemon._calendar_entry(
        _d(2026, 1, 9), _d(2026, 1, 11), "ftx", "Field Training Exercise", "1", NOW
    )
    assert past is True
    assert label == "09–11 JAN — BTBLK-01"


def test_calendar_future_ftx_is_not_struck(daemon):
    label, past = daemon._calendar_entry(
        _d(2026, 11, 13), _d(2026, 11, 15), "ftx", "Field Training Exercise", "2", NOW
    )
    assert past is False
    assert label == "13–15 NOV — BTBLK-02"


def test_calendar_mcftx_label(daemon):
    label, past = daemon._calendar_entry(
        _d(2026, 10, 8), _d(2026, 10, 11), "mcftx", "Multi-Company Field Training Exercise", "", NOW
    )
    assert past is True
    assert label == "08–11 OCT — MCFTX (Multi-Company)"


def test_calendar_urban_evasion_uses_title(daemon):
    label, past = daemon._calendar_entry(
        _d(2026, 11, 7), None, "ftx", "Urban Evasion Part Deux", "", NOW
    )
    assert past is False
    assert label == "07 NOV — Urban Evasion Part Deux"


def test_calendar_multi_block_labels_ftx(daemon):
    label, past = daemon._calendar_entry(
        _d(2026, 12, 11), _d(2026, 12, 13), "ftx", "Field Training Exercise", "1,3", NOW
    )
    assert label == "11–13 DEC — FTX"


# ─── TRADOC subject doc URL mapping (pure) ───────────────────────────────────

def test_tradoc_subject_url_none(daemon):
    assert daemon._tradoc_subject_url(1, "none", None, False) is None
    assert daemon._tradoc_subject_url(1, None, None, False) is None


def test_tradoc_subject_url_markdown_needs_body(daemon):
    assert daemon._tradoc_subject_url(7, "markdown", None, False) is None
    assert (
        daemon._tradoc_subject_url(7, "markdown", None, True)
        == "https://portal.13thlegion.org/training/tradoc/7"
    )


def test_tradoc_subject_url_page_relative_path(daemon):
    assert (
        daemon._tradoc_subject_url(2, "page", "/training/tradoc/drill-ceremony", False)
        == "https://portal.13thlegion.org/training/tradoc/drill-ceremony"
    )


def test_tradoc_subject_url_pdf_relative_path(daemon):
    assert (
        daemon._tradoc_subject_url(3, "pdf", "/static/tradoc/uniform-sop.pdf", False)
        == "https://portal.13thlegion.org/static/tradoc/uniform-sop.pdf"
    )


def test_tradoc_subject_url_external_absolute(daemon):
    url = "https://www.youtube.com/watch?v=rYfhBxCckXo"
    assert daemon._tradoc_subject_url(13, "external", url, False) == url


def test_tradoc_subject_url_no_doc_url(daemon):
    assert daemon._tradoc_subject_url(1, "page", None, False) is None
    assert daemon._tradoc_subject_url(1, "external", "", False) is None


# ─── Team leader resolution (pure name/format + fallback) ───────────────────

def test_team_leader_none_for_missing_team(daemon, monkeypatch):
    monkeypatch.setattr(
        daemon, "_resolve_portal_db_host", lambda: "localhost"
    )
    assert daemon._load_team_leader(None) is None
    assert daemon._load_team_leader("") is None
