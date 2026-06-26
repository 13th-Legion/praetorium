"""Newsletter rendering + delivery — shared by the route (send-now) and the
scheduler (scheduled sends). Keeps a single source of truth for recipient
resolution, HTML rendering, and SMTP assembly with attachments.
"""
from __future__ import annotations

import os
import re
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.member import Member
from app.newsletter_assets import crest_url, NEWSLETTER_ATTACH_DIR
from app.settings import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM

logger = logging.getLogger(__name__)

# Mirror of EMAIL_BLAST_GROUPS in s1_admin (kept here to avoid a circular import).
EMAIL_BLAST_GROUPS = {
    "entire_unit": {"label": "Entire Unit", "filter": ["active", "recruit"]},
    "patched": {"label": "Patched", "filter": ["active"]},
    "leaders": {"label": "Leaders (NCOs + Officers)", "nc_groups": ["Leaders"]},
    "team_leaders": {"label": "Team Leaders", "nc_groups": ["Leaders"]},
    "shop_leaders": {"label": "Shop Leaders (S1-S6)",
                     "nc_groups": ["[S-1]", "[S-2]", "[S-3]", "[S-4]", "[S-5]", "[S-6]"]},
    "command": {"label": "Command", "nc_groups": ["Command"]},
}


async def resolve_recipients(db: AsyncSession, group_keys: list[str]) -> list[tuple[str, str]]:
    """Return [(email, display_name)] for the union of the selected groups.
    Mirrors the email-blast logic exactly so newsletters and blasts target the
    same audiences."""
    member_ids: set[int] = set()
    for grp in group_keys:
        config = EMAIL_BLAST_GROUPS.get(grp)
        if not config:
            continue
        if "filter" in config:
            result = await db.execute(select(Member).where(Member.status.in_(config["filter"])))
            for m in result.scalars().all():
                if m.email:
                    member_ids.add(m.id)
        elif "nc_groups" in config:
            result = await db.execute(
                select(Member).where(Member.status.in_(["active", "recruit"]))
            )
            for m in result.scalars().all():
                member_ids.add(m.id)
    if not member_ids:
        return []
    result = await db.execute(select(Member).where(Member.id.in_(member_ids)))
    members = result.scalars().all()
    seen = set()
    out = []
    for m in members:
        if m.email and m.email.lower() not in seen:
            seen.add(m.email.lower())
            out.append((m.email, m.display_name))
    return out


def render_newsletter_html(title: str, body_html: str, crest_key: str, sender_name: str) -> str:
    """Wrap the editor body in the Legionary Dispatch masthead + footer shell.

    Layout matches the past issues: centered crest masthead, gold issue title,
    single-column body, signature footer with motto. Inline images in body_html
    already carry absolute /nlmedia URLs so they render in remote clients.
    """
    masthead_crest = crest_url(crest_key)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f0f0f3;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f0f0f3;"><tr><td align="center" style="padding:16px;">
<table role="presentation" width="660" cellpadding="0" cellspacing="0" style="max-width:660px;width:100%;background:#ffffff;border-radius:8px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;">

  <!-- Masthead -->
  <tr><td style="background:#1a1a2e;padding:24px 20px;text-align:center;">
    <img src="{masthead_crest}" alt="13th Legion" width="96" style="display:inline-block;max-width:96px;height:auto;margin-bottom:10px;">
    <h1 style="color:#d4a537;margin:0;font-size:26px;letter-spacing:.5px;font-family:Georgia,'Times New Roman',serif;">{title}</h1>
    <p style="color:#bbb;margin:6px 0 0;font-size:12px;">Texas State Militia &mdash; Dallas / Fort Worth</p>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:24px 28px;color:#1a1a2e;font-size:15px;line-height:1.6;">
    <style>
      .nl-body p {{ margin:0 0 .8em 0; }}
      .nl-body ul, .nl-body ol {{ margin:0 0 .8em 1.2em; padding:0; }}
      .nl-body h1, .nl-body h2, .nl-body h3 {{ color:#1f6feb; margin:1.1em 0 .4em; }}
      .nl-body a {{ color:#1f6feb; }}
      .nl-body img {{ max-width:100%; height:auto; border-radius:4px; }}
      .nl-body blockquote {{ border-left:3px solid #d4a537; margin:0 0 .8em; padding:.2em 0 .2em 12px; color:#444; }}
    </style>
    <div class="nl-body">
    {body_html}
    </div>
    <p style="margin-top:28px;color:#1a1a2e;">
      <em>Nunquam Non Paratus,</em><br>
      <strong>{sender_name}</strong><br>
      13th Legion, Texas State Militia
    </p>
  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#1a1a2e;padding:16px 20px;text-align:center;">
    <p style="color:#d4a537;margin:0;font-style:italic;font-size:13px;">Nunquam Non Paratus &mdash; Never Not Ready</p>
    <p style="color:#888;margin:6px 0 0;font-size:11px;">
      13th Legion &middot; Texas State Militia &middot;
      <a href="https://13thlegion.org" style="color:#888;">13thlegion.org</a>
    </p>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


def html_to_plaintext(body_html: str) -> str:
    t = re.sub(r"<br\s*/?>", "\n", body_html)
    t = re.sub(r"</p>\s*<p[^>]*>", "\n\n", t)
    t = re.sub(r"<li[^>]*>", "\n  • ", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def send_newsletter_sync(
    *,
    subject: str,
    title: str,
    body_html: str,
    crest_key: str,
    sender_name: str,
    recipients: list[tuple[str, str]],
    attachment_files: list[tuple[str, str, str]],  # (disk_path, orig_name, mime_type)
) -> tuple[int, int, str | None]:
    """Blocking SMTP send. Returns (sent, failed, error). Safe to call from a
    thread (send-now BackgroundTask) or the scheduler worker."""
    html_body = render_newsletter_html(title, body_html, crest_key, sender_name)
    plain_body = html_to_plaintext(body_html)

    # Pre-read attachment bytes once (reused for every recipient).
    loaded_atts = []
    for disk_path, orig_name, mime_type in attachment_files:
        try:
            with open(disk_path, "rb") as fh:
                loaded_atts.append((fh.read(), orig_name, mime_type))
        except Exception as e:
            logger.error(f"Newsletter attachment unreadable {disk_path}: {e}")

    sent = failed = 0
    err = None
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            if SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            for email_addr, _name in recipients:
                try:
                    if loaded_atts:
                        msg = MIMEMultipart("mixed")
                        alt = MIMEMultipart("alternative")
                        alt.attach(MIMEText(plain_body, "plain"))
                        alt.attach(MIMEText(html_body, "html"))
                        msg.attach(alt)
                        for data, oname, mtype in loaded_atts:
                            maintype, _, subtype = mtype.partition("/")
                            if maintype == "image":
                                part = MIMEImage(data, _subtype=subtype or "octet-stream")
                            elif mtype == "application/pdf":
                                part = MIMEApplication(data, _subtype="pdf")
                            else:
                                part = MIMEApplication(data)
                            part.add_header("Content-Disposition", "attachment", filename=oname)
                            msg.attach(part)
                    else:
                        msg = MIMEMultipart("alternative")
                        msg.attach(MIMEText(plain_body, "plain"))
                        msg.attach(MIMEText(html_body, "html"))
                    msg["Subject"] = subject
                    msg["From"] = SMTP_FROM
                    msg["To"] = email_addr
                    server.send_message(msg)
                    sent += 1
                except Exception as e:
                    failed += 1
                    logger.warning(f"Newsletter send failed to {email_addr}: {e}")
    except Exception as e:
        err = str(e)
        logger.error(f"Newsletter SMTP connection failed: {e}")
    logger.info(f"Newsletter '{subject}' complete: {sent} sent, {failed} failed")
    return sent, failed, err
