"""Shared email sender (Proton Bridge SMTP) — single source for all outbound mail.

Replaces the hand-rolled smtplib/MIME blocks copy-pasted across events.py,
s1_admin.py, member_edit.py, and newsletter_send.py (audit A5). Each of those
rebuilt MIMEMultipart + starttls + login + sendmail slightly differently (only
events.py reused the connection). This centralizes:

  * send_email(to, subject, html, text=None, ...)     — one message
  * send_bulk(messages)                               — reuse ONE connection

Config comes from app.settings (SMTP_HOST/PORT/USER/PASS/FROM). Auth user MUST
match the From address for Proton Bridge (it rejects mismatched senders) — the
default From is admin@13thlegion.org, matching SMTP_USER.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable, Optional

from app.settings import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM

log = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    to: str
    subject: str
    html: str
    text: Optional[str] = None
    from_addr: Optional[str] = None          # defaults to SMTP_FROM
    reply_to: Optional[str] = None
    extra_headers: dict = field(default_factory=dict)


def _build_mime(m: EmailMessage) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = m.subject
    msg["From"] = m.from_addr or SMTP_FROM
    msg["To"] = m.to
    if m.reply_to:
        msg["Reply-To"] = m.reply_to
    for k, v in (m.extra_headers or {}).items():
        msg[k] = v
    # Plain-text part first (fallback), then HTML (preferred) per RFC.
    if m.text:
        msg.attach(MIMEText(m.text, "plain"))
    msg.attach(MIMEText(m.html, "html"))
    return msg


def _connect() -> smtplib.SMTP:
    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
    server.starttls()
    if SMTP_PASS:
        server.login(SMTP_USER, SMTP_PASS)
    return server


def send_email(to: str, subject: str, html: str, text: Optional[str] = None,
               from_addr: Optional[str] = None, reply_to: Optional[str] = None,
               extra_headers: Optional[dict] = None) -> bool:
    """Send a single email. Returns True on success, False on failure (logged)."""
    m = EmailMessage(to=to, subject=subject, html=html, text=text,
                     from_addr=from_addr, reply_to=reply_to,
                     extra_headers=extra_headers or {})
    try:
        with _connect() as server:
            msg = _build_mime(m)
            server.sendmail(msg["From"], [to], msg.as_string())
        log.info(f"email sent to {to}: {subject!r}")
        return True
    except Exception as e:
        log.error(f"email send failed to {to} ({subject!r}): {e}")
        return False


def send_bulk(messages: Iterable[EmailMessage]) -> tuple[int, int]:
    """Send many emails over ONE connection. Returns (sent, failed).

    A per-message failure is logged and counted but does not abort the batch.
    """
    messages = list(messages)
    if not messages:
        return (0, 0)
    sent = failed = 0
    try:
        with _connect() as server:
            for m in messages:
                try:
                    msg = _build_mime(m)
                    server.sendmail(msg["From"], [m.to], msg.as_string())
                    sent += 1
                except Exception as e:
                    failed += 1
                    log.error(f"bulk email failed to {m.to} ({m.subject!r}): {e}")
    except Exception as e:
        # Connection-level failure — everything not yet sent is failed.
        log.error(f"bulk email connection failed: {e}")
        failed += len(messages) - sent
    log.info(f"bulk email: {sent} sent, {failed} failed")
    return (sent, failed)
