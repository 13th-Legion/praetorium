"""Nextcloud *user account* field sync (currently: email).

Why this exists
---------------
A member's email is stored in **two** places and nothing kept them in step:

  * ``members.email`` in the portal DB — the roster, source of truth for PII,
    and what Praetorium's own blasts (WARNO/OPORD/FRAGO, credentials) use.
  * the **Nextcloud account** address — what Nextcloud's *own* mail uses:
    notification digests, password-change notices, share notifications.

Editing a member's email in the portal only ever wrote the first one, so the
two silently diverged. Found 2026-09-10 via PFC Pope: his portal address was
correct and he was receiving Praetorium mail perfectly, while Nextcloud kept
sending digests to a stale ``@protonmail.com`` address that hard-bounced
``554 ... does not exist`` every few days. From the outside it looked like
"his email is broken"; in fact only one of the two systems was wrong. A
roster-wide audit then turned up several more.

This module pushes the roster address onto the NC account so the portal edit
form is genuinely the single place a member's email has to change.

Conventions follow ``nc_groups``: every call is **best-effort and logged** — a
Nextcloud hiccup must never 500 a portal save, and must never silently look
like success either.

⚠️ The OCS provisioning API returns **HTTP 200 even when the operation fails**;
the real result is the ``statuscode`` in the response body. Checking only the
HTTP status is how a rejected password reset once mailed out a password that
had never been set. ``ocs_status`` exists so that trap is handled in one place.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

_OCS_HEADERS = {"OCS-APIRequest": "true", "Accept": "application/json"}

# OCS meta statuscodes worth naming.
OCS_OK = (100, 200)
_OCS_HINTS = {
    101: "invalid or missing value",
    102: "value rejected by Nextcloud (policy?)",
    997: "Nextcloud rejected our service credentials (NC_API_USER/NC_API_PASSWORD)",
    998: "no such Nextcloud user",
}


def _auth():
    return (settings.nc_api_user, settings.nc_api_password)


def ocs_status(resp: httpx.Response) -> Optional[int]:
    """Return the OCS meta statuscode, or None if it can't be determined.

    Tries JSON first, then falls back to a regex over XML — a proxy error page
    or an XML-formatted response would otherwise parse as "no status", which is
    indistinguishable from success if the caller only checks resp.status_code.
    """
    try:
        return int(resp.json()["ocs"]["meta"]["statuscode"])
    except Exception:
        pass
    m = re.search(r"<statuscode>(\d+)</statuscode>", resp.text or "")
    return int(m.group(1)) if m else None


async def set_email(
    nc_username: str,
    email: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[bool, str]:
    """Set a Nextcloud account's email. Returns ``(ok, detail)``.

    Never raises: callers are portal save paths where a NC outage must not lose
    the user's edit. ``detail`` is a short human-readable reason on failure and
    is logged here as well, so a caller that ignores it still leaves a trail.
    """
    if not nc_username:
        return False, "no nc_username on this member"
    if not email:
        return False, "no email to set"

    url = f"{settings.nc_url}/ocs/v2.php/cloud/users/{nc_username}"
    payload = {"key": "email", "value": email}

    async def _put(c: httpx.AsyncClient) -> httpx.Response:
        return await c.put(
            url,
            auth=_auth(),
            headers=_OCS_HEADERS,
            params={"format": "json"},
            data=payload,
        )

    try:
        if client is not None:
            resp = await _put(client)
        else:
            async with httpx.AsyncClient(timeout=15) as c:
                resp = await _put(c)
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        log.error("NC email sync for %s failed to send: %s", nc_username, detail)
        return False, detail

    if resp.status_code != 200:
        detail = f"HTTP {resp.status_code}"
        log.error(
            "NC email sync for %s failed: %s body=%r",
            nc_username, detail, (resp.text or "")[:300],
        )
        return False, detail

    status = ocs_status(resp)
    if status is None:
        detail = "unreadable OCS response; email state unknown"
        log.error(
            "NC email sync for %s: %s body=%r",
            nc_username, detail, (resp.text or "")[:300],
        )
        return False, detail

    if status not in OCS_OK:
        detail = f"OCS {status} — {_OCS_HINTS.get(status, 'rejected by Nextcloud')}"
        log.error(
            "NC email sync for %s failed: %s body=%r",
            nc_username, detail, (resp.text or "")[:300],
        )
        return False, detail

    log.info("NC email synced for %s (OCS %s)", nc_username, status)
    return True, "ok"


async def get_email(
    nc_username: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[str]:
    """Read the email currently on a Nextcloud account, or None if unavailable.

    Used by the drift reconciler so it can report and fix without guessing.
    """
    if not nc_username:
        return None
    url = f"{settings.nc_url}/ocs/v2.php/cloud/users/{nc_username}"

    async def _get(c: httpx.AsyncClient) -> httpx.Response:
        return await c.get(url, auth=_auth(), headers=_OCS_HEADERS,
                           params={"format": "json"})

    try:
        if client is not None:
            resp = await _get(client)
        else:
            async with httpx.AsyncClient(timeout=15) as c:
                resp = await _get(c)
    except Exception as e:
        log.warning("NC email read for %s failed: %s", nc_username, e)
        return None

    if resp.status_code != 200 or ocs_status(resp) not in OCS_OK:
        log.warning(
            "NC email read for %s: HTTP %s OCS %s",
            nc_username, resp.status_code, ocs_status(resp),
        )
        return None
    try:
        return resp.json()["ocs"]["data"].get("email") or ""
    except Exception:
        return None


def emails_differ(roster: Optional[str], nextcloud: Optional[str]) -> bool:
    """True when the two addresses are *functionally* different.

    Compared case-insensitively on purpose. The roster-wide audit that prompted
    this found 17 "mismatches", but 11 were pure capitalisation
    (``CC082954@proton.me`` vs ``cc082954@proton.me``) which every real mail
    provider treats as the same mailbox. Treating those as drift would generate
    permanent false alarms and bury the handful that actually matter.
    """
    a = (roster or "").strip().lower()
    b = (nextcloud or "").strip().lower()
    return a != b
