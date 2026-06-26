"""Scheduled-newsletter worker.

A lightweight async loop started in the app lifespan (no APScheduler dependency,
consistent with the existing in-process style). Every POLL_SECONDS it looks for
newsletters whose status is 'scheduled' and whose scheduled_at <= now (UTC),
sends them via the shared sender, archives a rendered copy to Nextcloud, and
records the result.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app.database import async_session
from app.models.newsletter import Newsletter, NewsletterAttachment
from app.newsletter_assets import NEWSLETTER_ATTACH_DIR
from app.newsletter_send import (
    resolve_recipients, send_newsletter_sync, render_newsletter_html,
)
from app.settings import NC_SVC_USER as _NC_USER, NC_SVC_PASS as _NC_PASS

logger = logging.getLogger(__name__)

POLL_SECONDS = 60
NC_URL = "https://cloud.13thlegion.org"
# WebDAV base for the self-building archive (matches where past issues live).
ARCHIVE_DAV_BASE = (
    f"{NC_URL}/remote.php/dav/files/spooky/"
    "13th%20Legion%20Shared/%5bS-1%5d%20Admin/Legionary%20Dispatch"
)


async def _archive_to_nextcloud(nl: Newsletter, html: str) -> str | None:
    """Upload a rendered HTML copy of the sent newsletter to the Legionary
    Dispatch folder. Returns the NC path on success."""
    safe_title = (nl.title or f"Dispatch {nl.id}").replace("/", "-")
    filename = f"{safe_title}.html"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Ensure the folder exists (idempotent).
            await client.request("MKCOL", f"{ARCHIVE_DAV_BASE}/", auth=(_NC_USER, _NC_PASS))
            url = f"{ARCHIVE_DAV_BASE}/{quote(filename)}"
            resp = await client.put(url, content=html.encode("utf-8"), auth=(_NC_USER, _NC_PASS))
            if resp.status_code in (201, 204):
                return f"[S-1] Admin/Legionary Dispatch/{filename}"
            logger.warning(f"Newsletter archive PUT returned {resp.status_code}")
    except Exception as e:
        logger.error(f"Newsletter archive failed: {e}")
    return None


async def deliver_newsletter(nl_id: int) -> None:
    """Resolve recipients, send, archive, and persist results for one newsletter.
    Used by both the scheduler and the send-now path."""
    async with async_session() as db:
        nl = await db.get(Newsletter, nl_id)
        if not nl or nl.status in ("sending", "sent"):
            return
        nl.status = "sending"
        await db.commit()

        groups = [g for g in (nl.groups_csv or "").split(",") if g]
        recipients = await resolve_recipients(db, groups)

        att_rows = (await db.execute(
            select(NewsletterAttachment).where(NewsletterAttachment.newsletter_id == nl.id)
        )).scalars().all()
        attachment_files = [
            (str(NEWSLETTER_ATTACH_DIR / a.filename), a.orig_name, a.mime_type)
            for a in att_rows
        ]

        # Render once for archive.
        html = render_newsletter_html(nl.title, nl.body_html, nl.crest_key, nl.created_by_name)

    # SMTP send off the event loop (blocking).
    sent, failed, err = await asyncio.to_thread(
        send_newsletter_sync,
        subject=nl.subject,
        title=nl.title,
        body_html=nl.body_html,
        crest_key=nl.crest_key,
        sender_name=nl.created_by_name,
        recipients=recipients,
        attachment_files=attachment_files,
    )

    archive_path = await _archive_to_nextcloud(nl, html)

    async with async_session() as db:
        nl = await db.get(Newsletter, nl_id)
        if not nl:
            return
        nl.recipient_count = len(recipients)
        nl.sent_count = sent
        nl.failed_count = failed
        nl.error = err
        nl.archive_path = archive_path
        nl.sent_at = datetime.utcnow()
        nl.status = "failed" if (err and sent == 0) else "sent"
        await db.commit()
    logger.info(f"Newsletter {nl_id} delivered: {sent} sent / {failed} failed / archive={archive_path}")


async def _scan_once() -> None:
    async with async_session() as db:
        now = datetime.utcnow()
        due = (await db.execute(
            select(Newsletter.id).where(
                Newsletter.status == "scheduled",
                Newsletter.scheduled_at.isnot(None),
                Newsletter.scheduled_at <= now,
            )
        )).scalars().all()
    for nl_id in due:
        try:
            await deliver_newsletter(nl_id)
        except Exception as e:
            logger.error(f"Scheduled newsletter {nl_id} failed: {e}")


async def newsletter_scheduler_loop() -> None:
    logger.info("Newsletter scheduler started (poll=%ss)", POLL_SECONDS)
    while True:
        try:
            await _scan_once()
        except Exception as e:
            logger.error(f"Newsletter scheduler scan error: {e}")
        await asyncio.sleep(POLL_SECONDS)
