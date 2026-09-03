#!/usr/bin/env python3
"""
13th Legion S1 Recruit Pipeline Daemon

Watches Nextcloud Forms submissions and creates Deck cards.
Watches Deck card moves to "Approved — Onboarding" and triggers onboarding.

Usage:
    python3 recruit-daemon.py [--poll-interval 300] [--dry-run] [--once]

Runs as a systemd service on the NC server (167.172.233.122).
"""

import argparse
import json
import logging
import os
import re
import secrets
import smtplib
import string
import subprocess
import sys
import time
import urllib.request
import urllib.parse

# Uptime Kuma push heartbeat
KUMA_PUSH_URL = os.environ.get("KUMA_PUSH_URL", "")
from datetime import datetime
from email.mime.base import MIMEBase

# Systemd watchdog
try:
    from systemd.daemon import notify as sd_notify
except ImportError:
    sd_notify = lambda *a, **kw: None
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from urllib.parse import quote, unquote

import requests

# ─── Configuration ───────────────────────────────────────────────────────────

NC_URL = "https://cloud.13thlegion.org"
NC_USER = "spooky"
NC_PASS = os.environ.get("NC_SVC_PASS", "")

# Service account for provisioning (NC admin)
NC_SVC_USER = "portal-svc"
NC_SVC_PASS = os.environ.get("NC_PORTAL_SVC_PASS", "")

# SMTP via Proton Bridge (localhost)
SMTP_HOST = "127.0.0.1"
SMTP_PORT = 1025
SMTP_USER = "admin@13thlegion.org"
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = "13th Legion <admin@13thlegion.org>"

# Forms & Deck
FORM_ID = 3
BOARD_ID = 5

# ── Forms storage layout (SINGLE SOURCE OF TRUTH) ───────────────────────────
# Nextcloud Forms files land in the form owner's home under:
#   Forms/{FORM_ID} - {FORM_TITLE}/{submission_id}/<uploaded files>
# The "{FORM_ID} - " prefix is NOT optional.
#
# This used to be built three different ways in three places. Two hardcoded
# the prefixed string and worked; move_applicant_files() built
# "Forms/{form_title}" WITHOUT the prefix, so its PROPFIND 404'd every single
# time. Result: 20 "No Forms folder yet" log lines dating back to 2026-03-07,
# zero successful moves ever, and an empty [S-1] Admin/Applications/ folder.
# No applicant document was ever archived. Verified on production 2026-09-03:
# the prefixed path returns 207 with 39+ submission folders; the unprefixed
# path returns 404.
#
# Build Forms URLs ONLY through forms_root_url() / forms_submission_url().
FORM_TITLE = "Texas State Militia \u2014 Application & Background Check Release"
FORM_FOLDER_NAME = f"{FORM_ID} - {FORM_TITLE}"

# Destination for archived applicant documents.
APPLICATIONS_FOLDER = "13th Legion Shared/[S-1] Admin/Applications"

# How the archive step relocates documents: "copy" (WebDAV COPY, leaves the
# originals in Forms storage so the Forms UI still shows each submission's
# attachments) or "move" (WebDAV MOVE, the original intent).
# COPY is the default because this step had never once executed successfully
# before 2026-09-03 -- a destructive operation running for the first time
# against real member documents should be reversible. Flip to "move" once a
# few cycles have been observed archiving correctly.
ARCHIVE_VERB = os.environ.get("RECRUIT_ARCHIVE_VERB", "copy").upper()

# Onboarding retry policy. A card that fails onboarding this many times is
# moved to the dead-letter list and stops being retried, so one poisoned card
# can't spin forever. (John Garcia's card retried 1,942 times over 7 days in
# Aug-Sep 2026 and spammed the NC log the whole time.)
MAX_ONBOARD_ATTEMPTS = 5

# create_nc_account() outcomes
NC_ACCOUNT_CREATED = "created"
NC_ACCOUNT_EXISTS = "exists"

# Stack IDs (S1 — Recruit Pipeline board)
STACKS = {
    "new": 11,
    "background_check": 12,
    "interview": 13,
    "documents_payment": 14,
    "approved": 15,
    "complete": 16,
}

# Default groups for new recruits
RECRUIT_GROUPS = ["13th Legion", "Rank - Recruit"]

# Multi-company application routing
# "deck" = full 13th Legion pipeline; email address = forward to that S1; None = use fallback
COMPANY_ROUTING = {
    "13th Legion (DFW)": "deck",
    "Archangels (Corpus Christi)": "jbarnes0526@protonmail.com",
    "Guardians (Austin)": "jlacey2@protonmail.com",
    "Vikings (Houston)": "admin@tsmhouston.org",
    "Centurions (Brazoria)": "glassesM@protonmail.com",
}
STATE_S1_FALLBACK = "admin@texasstatemilitia.org"

# Praetorium portal DB (for creating member records)
def _resolve_portal_db_host():
    """Resolve praetorium-db container IP at runtime (Docker IPs are dynamic).

    Falls back to the last-known IP if docker inspect is unavailable."""
    import subprocess
    fallback = "172.21.0.2"
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
             "praetorium-db"],
            capture_output=True, text=True, timeout=10,
        )
        ip = out.stdout.strip()
        if ip:
            return ip
        _db_host_warn(
            f"docker inspect praetorium-db returned no IP "
            f"(rc={out.returncode} stderr={out.stderr.strip()[:200]!r}); "
            f"falling back to {fallback}"
        )
    except Exception as e:
        # Silent fallback here used to hide a dead/renamed DB container behind
        # a stale hardcoded IP, turning it into a confusing psycopg2 timeout
        # later. Say so out loud.
        _db_host_warn(
            f"docker inspect praetorium-db failed ({type(e).__name__}: {e}); "
            f"falling back to {fallback}"
        )
    return fallback


def _db_host_warn(msg):
    """Warn about DB-host resolution.

    Needed because _resolve_portal_db_host() can be called before setup_logging()
    has run; falls back to stderr, which systemd captures into the journal.
    """
    if "log" in globals():
        log.warning(msg)
    else:                                          # pragma: no cover
        print(f"[warn] {msg}", file=sys.stderr)


# NOTE: deliberately NOT resolved at import time. Both call sites invoke
# _resolve_portal_db_host() directly so a recreated praetorium-db container
# (new IP) is picked up without restarting the daemon.
PORTAL_DB_PORT = 5432
PORTAL_DB_NAME = "praetorium"
PORTAL_DB_USER = "praetorium"
PORTAL_DB_PASS = os.environ.get("POSTGRES_PASSWORD", "")

# Paths are env-overridable so the module can be imported and unit-tested off
# the server. Unset (i.e. in production) they resolve to the same values as
# before.
STATE_FILE = Path(os.environ.get("RECRUIT_STATE_FILE", "/opt/recruit-pipeline/state.json"))
LOG_DIR = Path(os.environ.get("RECRUIT_LOG_DIR", "/opt/recruit-pipeline"))

# Question ID → label mapping (from NC Form ID 3)
Q_MAP = {
    "28": "Company",
    "2": "Email",
    "3": "Legal Name",
    "6": "Sex",
    "7": "DOB",
    "8": "Address",
    "29": "County",
    "10": "Phone",
    "31": "Prior Service",
    "14": "Military Job",
    "16": "Medical Conditions",
    "17": "Felony/DV",
    "18": "Background Check Consent",
    "22": "How Heard",
    "23": "Referrer Name",
    "24": "Referrer Relationship",
    "25": "About Themselves",
}

# Geographic zone assignment: 6 equal 60° bearing slices from center point
# Center: I-30 & N Great Southwest Pkwy (32.7512, -97.0457)
import math as _math
GEO_CENTER = (32.7512, -97.0457)
GEO_ZONE_START = 330
GEO_ZONE_SIZE = 60
GEO_ZONE_TEAMS = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]

def _calc_bearing(lat, lon):
    lat1, lon1 = _math.radians(GEO_CENTER[0]), _math.radians(GEO_CENTER[1])
    lat2, lon2 = _math.radians(lat), _math.radians(lon)
    dlon = lon2 - lon1
    x = _math.sin(dlon) * _math.cos(lat2)
    y = (_math.cos(lat1) * _math.sin(lat2) -
         _math.sin(lat1) * _math.cos(lat2) * _math.cos(dlon))
    return (_math.degrees(_math.atan2(x, y)) + 360) % 360

def geo_assign_team(lat, lon):
    """Assign team based on bearing from center point."""
    b = _calc_bearing(lat, lon)
    idx = int(((b - GEO_ZONE_START + 360) % 360) / GEO_ZONE_SIZE)
    return GEO_ZONE_TEAMS[idx], b

DEFAULT_TEAM = "Alpha"  # Fallback if geocoding fails

# NC Talk room tokens for notifications
NCTALK_S1_ROOM = "r99dxzo8"  # T1 · S1 - Admin

# ─── Logging ─────────────────────────────────────────────────────────────────

def setup_logging():
    handlers = [logging.StreamHandler()]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.insert(0, logging.FileHandler(LOG_DIR / "recruit-daemon.log"))
    except OSError as e:
        # Importing the module for tests on a machine without /opt/recruit-pipeline
        # must not be fatal. In production this path is writable; if it ever
        # stops being writable we still get stderr -> journald.
        print(f"[warn] file logging disabled ({LOG_DIR}: {e}); logging to stderr only",
              file=sys.stderr)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("recruit-daemon")


log = setup_logging()


# ─── Helpers ─────────────────────────────────────────────────────────────────

# Transient HTTP statuses worth retrying: 5xx + Cloudflare-origin errors
# (520 unknown, 521 origin down, 522 conn timed out, 523 origin unreachable,
#  524 a timeout occurred), plus 429 rate-limit.
_RETRY_STATUSES = {429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
_NC_API_MAX_ATTEMPTS = 4          # 1 try + 3 retries
_NC_API_BACKOFF_BASE = 2.0        # seconds: 2, 4, 8 between attempts
_BODY_LOG_LIMIT = 300             # chars of response body kept in a failure log


def _body_excerpt(resp, limit=_BODY_LOG_LIMIT):
    """Whitespace-collapsed, truncated response body for logging.

    NEVER log a bare status code. Deck's attachment endpoint 400s with an
    EMPTY body when a required argument is missing (incident 2026-08-04 →
    2026-09-03: a month of silently-failing uploads), and the portal
    offboarding bugs were equally invisible. '<empty body>' is itself a
    diagnostic signal -- it means the AppFramework rejected the request
    before the controller ran.
    """
    try:
        raw = resp.content[: limit * 4]
        total = len(resp.content)
    except Exception as e:                        # pragma: no cover - defensive
        # Not silent: the failure text becomes the logged "body". This runs
        # inside logging, so it must never raise and mask the real error.
        return f"<unreadable body: {e}>"
    if not raw:
        return "<empty body>"
    try:
        text = raw.decode(resp.encoding or "utf-8", errors="replace")
    except (LookupError, TypeError):
        text = raw.decode("utf-8", errors="replace")
    text = " ".join(text.split())
    if len(text) > limit:
        return f"{text[:limit]!r}...(+{total - limit} more bytes)"
    return repr(text)


class NCRequestError(requests.exceptions.HTTPError):
    """HTTP error that carries the response body in its message.

    requests' own raise_for_status() throws away the body, which is exactly
    how the Deck attachment 400s stayed invisible for a month.
    """


def nc_request(method, url_or_endpoint, data=None, json_data=None, files=None,
               params=None, headers=None, user=None, passwd=None, auth=None,
               timeout=30, ocs=True, retry=True, expected_statuses=(),
               raise_for_status=False, label=None):
    """Single low-level HTTP entry point for every Nextcloud call.

    Returns the raw ``requests.Response`` so callers can inspect status, body
    and headers themselves -- unlike the old nc_api(), which called
    raise_for_status() and discarded the body.

    Handles:
      * absolute URLs *or* ``/ocs/...`` endpoints (NC_URL is prepended)
      * non-standard WebDAV verbs (PROPFIND / MKCOL / MOVE / COPY)
      * multipart uploads via ``files=``, query strings via ``params=``
      * custom credentials via ``user``/``passwd`` or a full ``auth`` tuple
      * retry + exponential backoff on 429 / 5xx / Cloudflare 52x and on
        connection errors and read timeouts (2s, 4s, 8s)

    Any non-2xx response is logged at WARNING with a truncated body, unless
    the status is listed in ``expected_statuses`` (e.g. a 404 that the caller
    treats as a normal "not found" answer).

    Set ``raise_for_status=True`` to raise NCRequestError -- which, unlike
    requests' HTTPError, includes the body in its message.
    """
    if url_or_endpoint.startswith("http://") or url_or_endpoint.startswith("https://"):
        url = url_or_endpoint
    else:
        url = f"{NC_URL}{url_or_endpoint}"
    tag = label or f"{method} {url_or_endpoint}"

    req_headers = {}
    if ocs:
        req_headers["OCS-APIRequest"] = "true"
        req_headers["Accept"] = "application/json"
    if json_data is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        # Caller wins; a None value removes a default (e.g. Accept on WebDAV).
        for k, v in headers.items():
            if v is None:
                req_headers.pop(k, None)
            else:
                req_headers[k] = v

    if auth is None:
        auth = (user or NC_USER, passwd or NC_PASS)

    max_attempts = _NC_API_MAX_ATTEMPTS if retry else 1
    last_exc = None
    resp = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.request(
                method, url,
                auth=auth, headers=req_headers,
                data=data, json=json_data, files=files, params=params,
                timeout=timeout,
            )
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            last_exc = e
            if attempt < max_attempts:
                wait = _NC_API_BACKOFF_BASE ** attempt
                log.warning(
                    "nc_request %s -> %s (transient), retry %d/%d in %.0fs",
                    tag, type(e).__name__, attempt, max_attempts - 1, wait,
                )
                time.sleep(wait)
                continue
            raise

        if resp.status_code in _RETRY_STATUSES and attempt < max_attempts:
            wait = _NC_API_BACKOFF_BASE ** attempt
            log.warning(
                "nc_request %s -> HTTP %s (transient), retry %d/%d in %.0fs body=%s",
                tag, resp.status_code, attempt, max_attempts - 1, wait,
                _body_excerpt(resp),
            )
            time.sleep(wait)
            continue
        break
    else:                                          # pragma: no cover - defensive
        if last_exc is not None:
            raise last_exc

    if resp is None:                               # pragma: no cover - defensive
        raise last_exc or RuntimeError(f"nc_request {tag}: no response and no error")

    ok = 200 <= resp.status_code < 300
    if not ok and resp.status_code not in expected_statuses:
        log.warning(
            "nc_request %s -> HTTP %s body=%s",
            tag, resp.status_code, _body_excerpt(resp),
        )

    if raise_for_status and not ok:
        raise NCRequestError(
            f"{tag} -> HTTP {resp.status_code} body={_body_excerpt(resp)}",
            response=resp,
        )

    return resp


def nc_api(method, endpoint, data=None, json_data=None, user=None, passwd=None,
           params=None, timeout=30):
    """JSON wrapper over nc_request(): returns the decoded body, raises on non-2xx.

    Thin by design -- retry/backoff/body-logging all live in nc_request().
    Kept with the original signature so existing call sites are unchanged.
    """
    resp = nc_request(
        method, endpoint,
        data=data, json_data=json_data, params=params,
        user=user, passwd=passwd, timeout=timeout,
        raise_for_status=True, label=f"nc_api {method} {endpoint}",
    )
    try:
        return resp.json()
    except ValueError as e:
        # A 2xx that isn't JSON is still a bug -- say what came back instead of
        # letting a bare JSONDecodeError bubble up with no context.
        raise NCRequestError(
            f"nc_api {method} {endpoint} -> HTTP {resp.status_code} "
            f"but body is not JSON ({e}) body={_body_excerpt(resp)}",
            response=resp,
        ) from e


# ─── Forms / WebDAV path builders ──────────────────────────────────────
#
# These are the ONLY sanctioned way to address Forms storage. See the
# FORM_FOLDER_NAME comment above for why.

def dav_base_url(user=None):
    """WebDAV files root for a user, e.g. .../remote.php/dav/files/spooky"""
    return f"{NC_URL}/remote.php/dav/files/{quote(user or NC_USER)}"


def forms_root_url(user=None):
    """Folder holding every submission for this form (prefixed with the form id)."""
    return f"{dav_base_url(user)}/Forms/{quote(FORM_FOLDER_NAME)}"


def forms_submission_url(sub_id, user=None):
    """Folder holding the uploaded files for one submission."""
    return f"{forms_root_url(user)}/{quote(str(sub_id))}"


def applicant_folder_name(name):
    """Applicant archive folder name: 'Last, First' / 'First Last' -> First_Last."""
    return name.replace(" ", "_").replace(",", "")


def applications_folder_url(name, user=None):
    """Destination folder for one applicant's archived documents."""
    return (f"{dav_base_url(user)}/{quote(APPLICATIONS_FOLDER)}"
            f"/{quote(applicant_folder_name(name))}")


def load_state():
    """Load daemon state."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "processed_submissions": [],
        "onboarded_cards": [],
        "last_check": 0,
    }


def save_state(state):
    """Persist daemon state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def generate_password(length=16):
    """Generate a secure random password."""
    chars = string.ascii_letters + string.digits + "!@#$%&"
    # Ensure at least one of each type
    pw = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%&"),
    ]
    pw += [secrets.choice(chars) for _ in range(length - 4)]
    secrets.SystemRandom().shuffle(pw)
    return "".join(pw)


def parse_answer(answers, q_id):
    """Extract a clean string from a form answer.
    
    answers can be:
      - a list of {"questionId": int, "text": str, ...} (NC Forms v3 API)
      - a dict keyed by question ID string (legacy)
    """
    q_id = int(q_id)
    if isinstance(answers, list):
        # NC Forms v3: list of answer objects
        texts = [a.get("text", "") for a in answers if a.get("questionId") == q_id]
        if not texts:
            return ""
        if len(texts) == 1:
            return str(texts[0]).strip()
        return ", ".join(str(t) for t in texts)
    # Legacy dict format
    val = answers.get(str(q_id), "")
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return str(val).strip()


# ─── Blacklist Check ─────────────────────────────────────────────────────────

def check_blacklist(name, email):
    """Check applicant against blacklisted members in portal DB.
    
    Returns:
        - ("exact", member_info) for exact name or email match
        - ("fuzzy", member_info) for close name match (Levenshtein ≤ 2)
        - (None, None) for no match
    """
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=_resolve_portal_db_host(), port=PORTAL_DB_PORT,
            dbname=PORTAL_DB_NAME, user=PORTAL_DB_USER, password=PORTAL_DB_PASS,
        )
        cur = conn.cursor()

        # Fetch all blacklisted members
        cur.execute("""
            SELECT id, first_name, last_name, email, personal_email,
                   separation_reason, separation_notes
            FROM members WHERE status = 'blacklisted'
        """)
        blacklisted = cur.fetchall()
        cur.close()
        conn.close()

        if not blacklisted:
            return None, None

        # Parse applicant name
        name_parts = name.strip().split()
        app_first = name_parts[0].lower() if name_parts else ""
        app_last = " ".join(name_parts[1:]).lower() if len(name_parts) > 1 else ""
        app_email = email.strip().lower()

        for row in blacklisted:
            mid, first, last, db_email, personal_email, reason, notes = row
            bl_first = (first or "").lower()
            bl_last = (last or "").lower()
            bl_email = (db_email or "").lower()
            bl_personal = (personal_email or "").lower()

            member_info = {
                "id": mid,
                "name": f"{first} {last}",
                "email": db_email,
                "personal_email": personal_email,
                "reason": reason,
                "notes": notes,
            }

            # Exact email match
            if app_email and app_email in (bl_email, bl_personal):
                return "exact", member_info

            # Exact name match
            if app_first == bl_first and app_last == bl_last:
                return "exact", member_info

            # Fuzzy name match (Levenshtein distance ≤ 2 on full name)
            app_full = f"{app_first} {app_last}"
            bl_full = f"{bl_first} {bl_last}"
            if _levenshtein(app_full, bl_full) <= 2 and len(app_full) > 4:
                return "fuzzy", member_info

        return None, None

    except ImportError:
        # FAIL-OPEN, LOUDLY. Returning (None, None) here would be
        # indistinguishable from "checked, and they're clean" -- the caller
        # would wave the applicant straight through. "unavailable" lets the
        # caller create the card anyway (a DB blip must not halt recruiting)
        # while telling S1 the screen did not actually run.
        log.error("psycopg2 not available — blacklist check DID NOT RUN")
        return "unavailable", None
    except Exception as e:
        log.error(f"Blacklist check FAILED for {name!r} <{email}>: {e}", exc_info=True)
        return "error", None


def _levenshtein(s1, s2):
    """Basic Levenshtein distance."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def notify_s1_nctalk(message):
    """Send a notification message to the S1 NC Talk room. Returns True on success."""
    try:
        resp = nc_request(
            "POST", f"/ocs/v2.php/apps/spreed/api/v1/chat/{NCTALK_S1_ROOM}",
            data={"message": message}, timeout=15,
            label="NC Talk S1 notification",
        )
        if resp.status_code in (200, 201):
            log.info("S1 notification sent to NC Talk")
            return True
        # nc_request already logged status + body.
        return False
    except Exception as e:
        log.error(f"Failed to send S1 NC Talk notification: {e}", exc_info=True)
        return False


def send_rejection_email(recipient_email, name):
    """Send a generic rejection email (no mention of blacklist)."""
    first_name = name.split()[0] if name.split() else name

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #1a1a2e; line-height: 1.6; max-width: 650px; margin: 0 auto;">

<div style="background: #1a1a2e; padding: 20px; text-align: center;">
    <table style="margin: 0 auto;" cellpadding="0" cellspacing="0"><tr>
        <td style="vertical-align: middle; padding-right: 15px;">
            <img src="https://13thlegion.org/assets/img/crest.png" alt="13th Legion" height="70" style="display: block;">
        </td>
        <td style="vertical-align: middle; text-align: center;">
            <h1 style="color: #d4a537; margin: 0; font-size: 28px;">13TH LEGION</h1>
            <p style="color: #ccc; margin: 5px 0 0;">Texas State Militia — Dallas / Fort Worth</p>
        </td>
        <td style="vertical-align: middle; padding-left: 15px;">
            <img src="https://13thlegion.org/assets/img/tsm-seal.png" alt="TSM" height="70" style="display: block;">
        </td>
    </tr></table>
</div>

<div style="padding: 20px;">
    <p>{first_name},</p>

    <p>Thank you for your interest in the Texas State Militia. After reviewing your application, we are unable to move forward with your candidacy at this time.</p>

    <p>We wish you the best in your future endeavors.</p>

    <p style="margin-top: 20px;">
        <em>Respectfully,</em><br>
        <strong>S1 — Personnel &amp; Recruiting</strong><br>
        13th Legion, Texas State Militia
    </p>
</div>

<div style="background: #1a1a2e; padding: 15px; text-align: center;">
    <p style="color: #888; margin: 0; font-size: 12px;">
        13th Legion · Texas State Militia · <a href="https://13thlegion.org" style="color: #888;">13thlegion.org</a>
    </p>
</div>

</body>
</html>"""

    plain = f"""{first_name},

Thank you for your interest in the Texas State Militia. After reviewing your application, we are unable to move forward with your candidacy at this time.

We wish you the best in your future endeavors.

Respectfully,
S1 — Personnel & Recruiting
13th Legion, Texas State Militia
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Application Status — Texas State Militia"
    msg["From"] = SMTP_FROM
    msg["To"] = recipient_email
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        log.info(f"Sent rejection email to {recipient_email}")
        return True
    except Exception as e:
        log.error(f"Failed to send rejection email to {recipient_email}: {e}")
        return False


# ─── Form → Deck Card (PP-017) ──────────────────────────────────────────────

def get_submissions():
    """Fetch all form submissions."""
    resp = nc_api("GET", f"/ocs/v2.php/apps/forms/api/v3/forms/{FORM_ID}/submissions")
    return resp.get("ocs", {}).get("data", {}).get("submissions", [])


def create_deck_card(submission):
    """Create a Deck card from a form submission."""
    answers = submission.get("answers", {})

    name = parse_answer(answers, "3") or "Unknown"
    email = parse_answer(answers, "2")
    county = parse_answer(answers, "29")
    phone = parse_answer(answers, "10")
    prior_service = parse_answer(answers, "31")
    military_job = parse_answer(answers, "14")

    # Build description
    desc_lines = []
    for q_id, label in Q_MAP.items():
        val = parse_answer(answers, q_id)
        if val:
            desc_lines.append(f"**{label}:** {val}")

    # Proton Mail field (S1 fills this in when recruit provides it)
    desc_lines.append("")
    desc_lines.append("---")
    desc_lines.append("**📧 Proton Mail:** _(pending — applicant must provide before approval)_")

    # Add suggested team based on geographic zone (bearing from center point)
    suggested_team = DEFAULT_TEAM
    geo_note = f"county: {county}"
    raw_addr = parse_answer(answers, 6)  # Address field id
    addr_for_geo = raw_addr or city or ""
    glat, glon = None, None
    if addr_for_geo:
        glat, glon = geocode_address(addr_for_geo)
    # Fallback: try city + zip if full address didn't resolve
    if glat is None and city:
        glat, glon = geocode_address(f"{city}, TX")
    if glat is not None:
        suggested_team, bearing = geo_assign_team(glat, glon)
        geo_note = f"bearing: {bearing:.1f}°"
    else:
        log.warning(f"Geocoding failed for all address variants, defaulting to {DEFAULT_TEAM}")
    desc_lines.append("")
    desc_lines.append("---")
    desc_lines.append(f"*Suggested Team:* **{suggested_team}** ({geo_note})")
    desc_lines.append(f"*Submitted:* {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    desc_lines.append(f"*Submission ID:* {submission.get('id')}")

    desc = "\n".join(desc_lines)

    card_data = {
        "title": f"📋 {name}",
        "type": "plain",
        "order": 0,
        "description": desc,
    }

    resp = nc_api(
        "POST",
        f"/index.php/apps/deck/api/v1.0/boards/{BOARD_ID}/stacks/{STACKS['new']}/cards",
        json_data=card_data,
    )
    card_id = resp.get("id")
    log.info(f"Created Deck card: '{name}' → New Application stack (card #{card_id})")

    # Attach uploaded files to the Deck card
    if card_id:
        attach_submission_files(submission, card_id, STACKS["new"])

    return resp


def list_submission_files(sub_id, label=""):
    """WebDAV hrefs of every uploaded file for one Forms submission.

    Returns a list of hrefs (may be empty). Returns None if the submission
    folder does not exist, so callers can tell "no folder" from "no files".

    Depth 3, not 1: Forms nests uploads inside a per-question subfolder
    ('30 - Please upload copies of your driver license...'), so a Depth-1
    listing of the submission folder finds only that subfolder and no files.
    """
    src = forms_submission_url(sub_id)
    r = nc_request(
        "PROPFIND", src, ocs=False, headers={"Depth": "3"},
        expected_statuses=(404,),
        label=f"PROPFIND forms submission #{sub_id}{label}",
    )
    if r.status_code == 404:
        return None
    if r.status_code != 207:
        # nc_request already logged status + body.
        return None
    hrefs = re.findall(r"<d:href>([^<]+)</d:href>", r.text)
    return [h for h in hrefs if not h.endswith("/")]


def attach_submission_files(submission, card_id, stack_id):
    """Download a submission's uploaded files and attach them to its Deck card.

    Returns the number of files successfully attached.
    """
    answers = submission.get("answers", [])
    if not isinstance(answers, list):
        return 0
    if not any(a.get("fileId") for a in answers):
        return 0

    sub_id = submission.get("id")
    attached = 0

    try:
        file_hrefs = list_submission_files(sub_id)
        if file_hrefs is None:
            log.warning(
                f"No Forms folder for submission #{sub_id} "
                f"({forms_submission_url(sub_id)})"
            )
            return 0
        if not file_hrefs:
            log.info(f"Submission #{sub_id} has no uploaded files to attach")
            return 0

        for href in file_hrefs:
            filename = unquote(href.split("/")[-1])
            dl = nc_request("GET", f"{NC_URL}{href}", ocs=False,
                            label=f"download {filename} (submission #{sub_id})")
            if dl.status_code != 200:
                # nc_request already logged status + body.
                continue

            # Attach to Deck card.
            #
            # `data` is REQUIRED. Deck's AttachmentApiController::create is
            # `create(int $cardId, string $type, string $data)` -- with no
            # default on $data, so omitting it makes Nextcloud's AppFramework
            # reject the request with a bare HTTP 400 and an EMPTY body,
            # *before* the controller runs. Nothing is written to
            # nextcloud.log, which is why this went unnoticed from ~2026-08-04
            # until Archer reported missing attachments on 2026-09-03: every
            # upload had been silently 400ing for a month.
            # For type=deck_file, `data` is the filename.
            # tests/test_recruit_daemon.py guards this argument.
            r2 = nc_request(
                "POST",
                f"/index.php/apps/deck/api/v1.0/boards/{BOARD_ID}/stacks/"
                f"{stack_id}/cards/{card_id}/attachments",
                ocs=False, headers={"OCS-APIRequest": "true"},
                data={"type": "deck_file", "data": filename},
                files={"file": (filename, dl.content)},
                label=f"attach {filename} to card #{card_id}",
            )
            if r2.status_code in (200, 201):
                attached += 1
                log.info(f"Attached file to card #{card_id}: {filename}")
            # Failures already logged with body by nc_request.

        return attached

    except Exception as e:
        log.error(
            f"Error attaching files to card #{card_id} (submission #{sub_id}): {e}",
            exc_info=True,
        )
        return attached


def send_application_received_email(recipient_email, name):
    """Send 'application received' confirmation to applicant with Proton Mail instructions."""

    first_name = name.split()[0] if name.split() else name

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #1a1a2e; line-height: 1.6; max-width: 650px; margin: 0 auto;">

<div style="background: #1a1a2e; padding: 20px; text-align: center;">
    <table style="margin: 0 auto;" cellpadding="0" cellspacing="0"><tr>
        <td style="vertical-align: middle; padding-right: 15px;">
            <img src="https://13thlegion.org/assets/img/crest.png" alt="13th Legion" height="70" style="display: block;">
        </td>
        <td style="vertical-align: middle; text-align: center;">
            <h1 style="color: #d4a537; margin: 0; font-size: 28px;">13TH LEGION</h1>
            <p style="color: #ccc; margin: 5px 0 0;">Texas State Militia — Dallas / Fort Worth</p>
        </td>
        <td style="vertical-align: middle; padding-left: 15px;">
            <img src="https://13thlegion.org/assets/img/tsm-seal.png" alt="TSM" height="70" style="display: block;">
        </td>
    </tr></table>
</div>

<div style="padding: 20px;">
    <p>{first_name},</p>

    <p>Thank you for your interest in the Texas State Militia. We've received your application and it is now under review by our S1 (Personnel) shop.</p>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        📬 What Happens Next
    </h3>
    <ol>
        <li><strong>Background check</strong> — We'll run a routine check based on the information you provided.</li>
        <li><strong>Interview</strong> — A member of our leadership team will reach out to schedule a brief phone or video call.</li>
        <li><strong>Approval &amp; onboarding</strong> — Once approved, you'll receive a welcome packet with your unit assignment, Nextcloud account, training calendar, and everything you need for your first FTX.</li>
    </ol>
    <p>This process typically takes <strong>1–2 weeks</strong>. We'll keep you posted at every step.</p>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        📧 Action Required: Create a Proton Mail Account
    </h3>
    <p>All TSM members are required to use a <strong>Proton Mail</strong> email address for unit communications. Proton Mail is a free, end-to-end encrypted email service based in Switzerland — it keeps our communications secure and private.</p>

    <p><strong>Please create your Proton Mail account now</strong> so it's ready when you're approved. This will speed up your onboarding significantly.</p>

    <table style="margin: 15px 0; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px 15px; background: #d4a537; border-radius: 4px;">
                <a href="https://account.proton.me/signup" style="color: #1a1a2e; text-decoration: none; font-weight: bold; font-size: 15px;">
                    Create Free Proton Mail Account →
                </a>
            </td>
        </tr>
    </table>

    <p style="font-size: 13px; color: #555;"><strong>Tips:</strong></p>
    <ul style="font-size: 13px; color: #555;">
        <li>The free tier is all you need — no paid plan required.</li>
        <li>Pick a professional-ish address (e.g., <em>firstname.lastname@proton.me</em>).</li>
        <li>Download the <strong>Proton Mail</strong> app on your phone: <a href="https://apps.apple.com/app/proton-mail/id979659905">iOS</a> · <a href="https://play.google.com/store/apps/details?id=ch.protonmail.android">Android</a></li>
        <li>Once you have it, <strong>reply to this email from your new Proton Mail address</strong> so we have it on file. <strong>Please include your First and Last name in the subject line</strong> so we know who you are.</li>
    </ul>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        ❓ Questions?
    </h3>
    <p>If you have any questions about the process or the unit, reply to this email and our recruiting team will get back to you.</p>

    <p>We look forward to having you with us.</p>

    <p style="margin-top: 20px;">
        <em>Nunquam Non Paratus,</em><br>
        <strong>S1 — Personnel &amp; Recruiting</strong><br>
        13th Legion, Texas State Militia
    </p>
</div>

<div style="background: #1a1a2e; padding: 15px; text-align: center;">
    <p style="color: #d4a537; margin: 0; font-style: italic;">
        Nunquam Non Paratus — Never Not Ready
    </p>
    <p style="color: #888; margin: 5px 0 0; font-size: 12px;">
        13th Legion · Texas State Militia · <a href="https://13thlegion.org" style="color: #888;">13thlegion.org</a>
    </p>
</div>

</body>
</html>"""

    plain = f"""{first_name},

Thank you for your interest in the Texas State Militia. We've received your application and it is now under review by our S1 (Personnel) shop.

WHAT HAPPENS NEXT
  1. Background check — routine check based on your application info.
  2. Interview — a member of our leadership team will reach out for a brief call.
  3. Approval & onboarding — you'll receive a welcome packet with your unit
     assignment, Nextcloud account, training calendar, and everything you need
     for your first FTX.

This process typically takes 1-2 weeks. We'll keep you posted.

ACTION REQUIRED: CREATE A PROTON MAIL ACCOUNT
All TSM members are required to use a Proton Mail email address for unit
communications. Proton Mail is a free, end-to-end encrypted email service
that keeps our communications secure and private.

Please create your account now so it's ready when you're approved:
  https://account.proton.me/signup

Tips:
  - The free tier is all you need — no paid plan required.
  - Pick a professional-ish address (e.g., firstname.lastname@proton.me).
  - Download the Proton Mail app on your phone.
  - Once you have it, REPLY TO THIS EMAIL from your new Proton Mail address
    so we have it on file. Please include your First and Last name in the
    subject line so we know who you are.

QUESTIONS?
Reply to this email and our recruiting team will get back to you.

We look forward to having you with us.

Nunquam Non Paratus,
S1 — Personnel & Recruiting
13th Legion, Texas State Militia
13thlegion.org
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Application Received — 13th Legion, Texas State Militia"
    msg["From"] = SMTP_FROM
    msg["To"] = recipient_email
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        log.info(f"Sent application received email to {recipient_email}")
        return True
    except Exception as e:
        log.error(f"Failed to send application received email to {recipient_email}: {e}")
        return False


def send_generic_application_received_email(recipient_email, name, company, company_email=None):
    """Send generic TSM-branded application received confirmation for non-13th applicants."""

    first_name = name.split()[0] if name.split() else name
    company_display = company or "your local unit"
    contact_email = company_email or STATE_S1_FALLBACK

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #1a1a2e; line-height: 1.6; max-width: 650px; margin: 0 auto;">

<div style="background: #1a1a2e; padding: 20px; text-align: center;">
    <table style="margin: 0 auto;" cellpadding="0" cellspacing="0"><tr>
        <td style="vertical-align: middle; text-align: center;">
            <img src="https://13thlegion.org/assets/img/tsm-seal.png" alt="TSM" height="80" style="display: block; margin: 0 auto;">
            <h1 style="color: #d4a537; margin: 10px 0 0; font-size: 24px;">TEXAS STATE MILITIA</h1>
        </td>
    </tr></table>
</div>

<div style="padding: 20px;">
    <p>{first_name},</p>

    <p>Thank you for your interest in the <strong>Texas State Militia</strong>. We have received your application and it has been forwarded to the leadership of the <strong>{company_display}</strong> for review.</p>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        What Happens Next
    </h3>
    <ol>
        <li><strong>Application review</strong> &mdash; Your local unit leadership will review your application.</li>
        <li><strong>Background check</strong> &mdash; A routine check will be conducted based on the information you provided.</li>
        <li><strong>Interview</strong> &mdash; A member of your local unit's leadership team will reach out to schedule a brief phone or video call.</li>
        <li><strong>Approval &amp; onboarding</strong> &mdash; Once approved, your unit will provide you with all the information you need to get started.</li>
    </ol>
    <p>This process typically takes <strong>1&ndash;2 weeks</strong>. Your local unit leadership will keep you posted.</p>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        Action Required: Create a Proton Mail Account
    </h3>
    <p>All TSM members are required to use a <strong>Proton Mail</strong> email address for unit communications. Proton Mail is a free, end-to-end encrypted email service based in Switzerland that keeps our communications secure and private.</p>

    <p><strong>Please create your Proton Mail account now</strong> so it is ready when you are approved.</p>

    <table style="margin: 15px 0; border-collapse: collapse;">
        <tr>
            <td style="padding: 8px 15px; background: #d4a537; border-radius: 4px;">
                <a href="https://account.proton.me/signup" style="color: #1a1a2e; text-decoration: none; font-weight: bold; font-size: 15px;">
                    Create Free Proton Mail Account
                </a>
            </td>
        </tr>
    </table>

    <ul style="font-size: 13px; color: #555;">
        <li>The free tier is all you need.</li>
        <li>Pick a professional address (e.g., <em>firstname.lastname@proton.me</em>).</li>
        <li>Download the Proton Mail app: <a href="https://apps.apple.com/app/proton-mail/id979659905">iOS</a> / <a href="https://play.google.com/store/apps/details?id=ch.protonmail.android">Android</a></li>
        <li>Once created, <strong>email your new Proton Mail address to {contact_email}</strong> so your unit has it on file. <strong>Please include your first and last name in the subject line</strong> so they know who you are.</li>
    </ul>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        Questions?
    </h3>
    <p style="background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; padding: 10px; font-size: 13px;">&#9888; <strong>Do not reply to this email</strong> &mdash; this inbox is not monitored by your unit.</p>
    <p>To reach <strong>{company_display}</strong>, email: <a href="mailto:{contact_email}" style="color: #d4a537; font-weight: bold;">{contact_email}</a></p>

    <p>We look forward to having you with us.</p>

    <p style="margin-top: 20px;">
        <strong>Recruiting</strong><br>
        Texas State Militia
    </p>
</div>

<div style="background: #1a1a2e; padding: 15px; text-align: center;">
    <p style="color: #d4a537; margin: 0; font-style: italic;">
        Serving the Citizens of Texas
    </p>
    <p style="color: #888; margin: 5px 0 0; font-size: 12px;">
        Texas State Militia &middot; <a href="https://texasstatemilitia.org" style="color: #888;">texasstatemilitia.org</a>
    </p>
</div>

</body>
</html>"""

    plain = f"""{first_name},

Thank you for your interest in the Texas State Militia. We have received your application and it has been forwarded to the leadership of the {company_display} for review.

WHAT HAPPENS NEXT
  1. Application review by your local unit leadership.
  2. Background check based on your application info.
  3. Interview with a member of your local unit's leadership team.
  4. Approval and onboarding with everything you need to get started.

This process typically takes 1-2 weeks. Your local unit leadership will keep you posted.

ACTION REQUIRED: CREATE A PROTON MAIL ACCOUNT
All TSM members are required to use a Proton Mail email address for unit
communications. Please create your account now:
  https://account.proton.me/signup

Tips:
  - The free tier is all you need.
  - Pick a professional address (e.g., firstname.lastname@proton.me).
  - Download the Proton Mail app on your phone.
  - Once created, EMAIL YOUR NEW PROTON MAIL ADDRESS to {contact_email}
    so your unit has it on file. Include your first and last name in the
    subject line so they know who you are.

QUESTIONS?
** Do not reply to this email -- this inbox is not monitored by your unit. **
To reach {company_display}, email: {contact_email}

We look forward to having you with us.

Recruiting
Texas State Militia
texasstatemilitia.org
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Application Received \u2014 Texas State Militia"
    msg["From"] = SMTP_FROM
    msg["To"] = recipient_email
    msg["Reply-To"] = contact_email
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        log.info(f"Sent generic application received email to {recipient_email} ({company_display})")
        return True
    except Exception as e:
        log.error(f"Failed to send generic application received email to {recipient_email}: {e}")
        return False


def forward_application_to_unit(sub, company, recipient_email):
    """Forward a non-13th application to the appropriate unit S1 via email."""
    answers = sub.get("answers", {})
    name = parse_answer(answers, "3") or "Unknown"
    applicant_email = parse_answer(answers, "2") or "Not provided"
    phone = parse_answer(answers, "10") or "Not provided"
    county = parse_answer(answers, "29") or "Not provided"
    prior_service = parse_answer(answers, "31") or "Not provided"
    about = parse_answer(answers, "25") or "Not provided"
    how_heard = parse_answer(answers, "22") or "Not provided"

    # Build all fields for the forwarding email
    field_lines = []
    for q_id, label in sorted(Q_MAP.items(), key=lambda x: x[1]):
        val = parse_answer(answers, q_id)
        if val:
            field_lines.append(f"<tr><td style='padding:4px 10px;font-weight:bold;vertical-align:top;'>{label}</td><td style='padding:4px 10px;'>{val}</td></tr>")

    fields_html = "\n".join(field_lines)

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #1a1a2e; line-height: 1.6; max-width: 650px; margin: 0 auto;">

<div style="background: #1a1a2e; padding: 20px; text-align: center;">
    <h1 style="color: #d4a537; margin: 0; font-size: 24px;">New TSM Application — {company}</h1>
    <p style="color: #ccc; margin: 5px 0 0;">Forwarded by 13th Legion Recruit Pipeline</p>
</div>

<div style="padding: 20px;">
    <p>A new application has been submitted for <strong>{company}</strong> via the TSM recruitment form.</p>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">Applicant Information</h3>
    <table style="border-collapse: collapse; width: 100%;">
        {fields_html}
    </table>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">Next Steps</h3>
    <p>This applicant has been sent an automated acknowledgment email with instructions to create a Proton Mail account. Please follow your unit's onboarding procedures to process this application.</p>

    <p style="margin-top: 20px; color: #666; font-size: 12px;">
        <em>This email was automatically forwarded by the 13th Legion S1 recruit pipeline.<br>
        If you believe this was sent in error, contact admin@13thlegion.org.</em>
    </p>
</div>
</body>
</html>"""

    try:
        msg = MIMEMultipart("mixed")
        msg["From"] = SMTP_FROM
        msg["To"] = recipient_email
        # CC state admin on all unit-forwarded applications
        recipients = [recipient_email]
        if recipient_email != STATE_S1_FALLBACK:
            msg["Cc"] = STATE_S1_FALLBACK
            recipients.append(STATE_S1_FALLBACK)
        msg["Subject"] = f"New TSM Application: {name} — {company}"
        msg.attach(MIMEText(html, "html"))

        # Attach uploaded files from Forms storage
        try:
            sub_id = sub.get("id")
            file_hrefs = list_submission_files(sub_id, label=" (email forward)")
            if file_hrefs is None:
                log.warning(
                    f"No Forms folder for submission #{sub_id} "
                    f"({forms_submission_url(sub_id)}), forwarding without attachments"
                )
            else:
                for href in file_hrefs:
                    filename = unquote(href.split("/")[-1])
                    dl = nc_request("GET", f"{NC_URL}{href}", ocs=False,
                                    label=f"download {filename} for forwarding")
                    if dl.status_code == 200:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(dl.content)
                        encoders.encode_base64(part)
                        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
                        msg.attach(part)
                        log.info(f"Attached file to forwarding email: {filename}")
                    # Download failures already logged with body by nc_request.
        except Exception as fe:
            log.warning(
                f"Error attaching files to forwarding email for submission "
                f"#{sub.get('id')}: {fe} — sending without attachments",
                exc_info=True,
            )

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, recipients, msg.as_string())

        log.info(f"Forwarded application for {name} ({company}) to {recipient_email}" +
                 (f" (cc: {STATE_S1_FALLBACK})" if recipient_email != STATE_S1_FALLBACK else ""))
        return True
    except Exception as e:
        log.error(f"Failed to forward application for {name} ({company}) to {recipient_email}: {e}")
        return False


def check_new_submissions(state, dry_run=False):
    """Check for new form submissions and route by company."""
    submissions = get_submissions()
    new_count = 0

    for sub in submissions:
        sub_id = sub.get("id")
        if sub_id in state["processed_submissions"]:
            continue

        answers = sub.get("answers", {})
        name = parse_answer(answers, "3") or f"submission #{sub_id}"
        email = parse_answer(answers, "2")
        company = parse_answer(answers, "28") or ""
        log.info(f"New submission found: {name} (#{sub_id}) — Company: {company}")

        if not dry_run:
            # ── Blacklist check (all companies) ──
            bl_match, bl_info = check_blacklist(name, email)
            if bl_match == "exact":
                log.warning(f"🚫 BLACKLIST EXACT MATCH: {name} ({email}) → {bl_info['name']} (ID {bl_info['id']})")
                # Auto-reject: send rejection email, notify S1, skip pipeline
                if email:
                    send_rejection_email(email, name)
                notify_s1_nctalk(
                    f"🚫 **Blacklist Hit — Auto-Rejected**\n"
                    f"Applicant: {name} ({email})\n"
                    f"Matched: {bl_info['name']} (ID {bl_info['id']})\n"
                    f"Original reason: {bl_info.get('reason', 'N/A')}\n"
                    f"Submission #{sub_id} blocked — rejection email sent."
                )
                state["processed_submissions"].append(sub_id)
                new_count += 1
                continue

            if bl_match in ("error", "unavailable"):
                # The screen did not run. Create the card so recruiting keeps
                # moving, but make sure a human knows it wasn't checked.
                log.error(
                    f"Blacklist screen did not run for {name} ({email}) — "
                    f"submission #{sub_id} is UNSCREENED"
                )
                notify_s1_nctalk(
                    f"⚠️ **Blacklist check did not run**\n"
                    f"Applicant: {name} ({email})\n"
                    f"Submission #{sub_id} — reason: `{bl_match}`\n"
                    f"The card was still created. **Screen this applicant manually.**"
                )

            if bl_match == "fuzzy":
                log.warning(f"⚠️ BLACKLIST FUZZY MATCH: {name} ({email}) ≈ {bl_info['name']} (ID {bl_info['id']})")
                # Flag for S1 review but still create the card
                notify_s1_nctalk(
                    f"⚠️ **Blacklist Fuzzy Match — Needs Review**\n"
                    f"Applicant: {name} ({email})\n"
                    f"Similar to: {bl_info['name']} (ID {bl_info['id']})\n"
                    f"Original reason: {bl_info.get('reason', 'N/A')}\n"
                    f"Card created in pipeline — please verify and take action."
                )

            # Route by company
            route = COMPANY_ROUTING.get(company, None)

            if route == "deck":
                # 13th Legion — full pipeline
                try:
                    create_deck_card(sub)
                except Exception as e:
                    log.error(
                        f"Failed to create card for submission #{sub_id} "
                        f"({name}): {e}", exc_info=True,
                    )
                    # NOT marked processed: leave it for the next poll to retry.
                    continue
            else:
                # Other company — forward to unit S1 or state fallback
                forward_to = route if route else STATE_S1_FALLBACK
                forward_application_to_unit(sub, company, forward_to)

            # Send application received confirmation — 13th gets branded, others get generic TSM
            if email:
                if route == "deck":
                    send_application_received_email(email, name)
                else:
                    company_email = route if route else STATE_S1_FALLBACK
                    send_generic_application_received_email(email, name, company, company_email=company_email)
            else:
                log.warning(f"No email for submission #{sub_id} ({name}), skipping confirmation email")

        state["processed_submissions"].append(sub_id)
        new_count += 1

    return new_count


# ─── Onboarding (PP-018) ────────────────────────────────────────────────────

def get_stack_cards(stack_id):
    """Get all cards in a Deck stack."""
    resp = nc_api(
        "GET",
        f"/index.php/apps/deck/api/v1.0/boards/{BOARD_ID}/stacks/{stack_id}",
    )
    return resp.get("cards", [])


def parse_card_for_onboarding(card):
    """Extract member info from a Deck card description."""
    desc = card.get("description", "")
    info = {"raw_title": card.get("title", "")}

    for line in desc.split("\n"):
        line = line.strip()
        if line.startswith("**") and ":**" in line:
            key = line.split(":**")[0].replace("**", "").strip()
            val = line.split(":**", 1)[1].strip()
            val = val.strip("*_~")  # strip markdown bold/italic
            info[key] = val

    return info


def _ocs_section(resp, section):
    """resp.json()['ocs'][section] as a dict, or {} if the body isn't OCS JSON.

    Never raises: OCS endpoints answer with HTML error pages often enough that
    a bare JSONDecodeError here would mask the real status.
    """
    try:
        return resp.json().get("ocs", {}).get(section, {}) or {}
    except (ValueError, AttributeError):
        return {}


def nc_user_exists(username):
    """Does this NC account exist?  True / False / None (couldn't tell).

    Used to disambiguate a create call that failed with a TIMEOUT, where NC may
    have completed the creation anyway.
    """
    try:
        r = nc_request(
            "GET", f"/ocs/v2.php/cloud/users/{quote(username)}",
            user=NC_SVC_USER, passwd=NC_SVC_PASS,
            expected_statuses=(404,),      # a normal "no such user" answer
            label=f"lookup NC user {username}",
        )
        if r.status_code == 404:
            return False
        meta = _ocs_section(r, "meta")
        status = meta.get("statuscode", 0)
        if status in (100, 200):
            return True
        if status in (404, 998):
            return False
        log.warning(
            f"Unexpected OCS status {status} checking NC user {username} "
            f"(HTTP {r.status_code} body={_body_excerpt(r)})"
        )
        return None
    except Exception as e:
        log.warning(f"Could not check whether NC account {username} exists: {e}",
                    exc_info=True)
        return None


def nc_user_never_logged_in(username):
    """True if the account has never been logged into.  True / False / None.

    Gate for whether it's safe to reset a password during onboarding recovery:
    if the member has already logged in they may have chosen their own password
    and we must not clobber it.
    """
    try:
        r = nc_request(
            "GET", f"/ocs/v2.php/cloud/users/{quote(username)}",
            user=NC_SVC_USER, passwd=NC_SVC_PASS,
            expected_statuses=(404,),
            label=f"lastLogin for {username}",
        )
        data = _ocs_section(r, "data")
        if "lastLogin" not in data:
            # Unknown, NOT "never logged in": the caller must not reset a
            # password on the strength of a body it couldn't read.
            log.warning(
                f"No lastLogin field for {username} (HTTP {r.status_code} "
                f"body={_body_excerpt(r)}) — treating as UNKNOWN"
            )
            return None
        return int(data.get("lastLogin") or 0) == 0
    except Exception as e:
        log.warning(f"Could not read lastLogin for {username}: {e}", exc_info=True)
        return None


def set_nc_password(username, password):
    """Set an existing NC account's password. Returns True on success."""
    try:
        r = nc_request(
            "PUT", f"/ocs/v2.php/cloud/users/{quote(username)}",
            data={"key": "password", "value": password},
            user=NC_SVC_USER, passwd=NC_SVC_PASS,
            # OCS reports failures in the body with HTTP 400/996-ish; we log
            # the body ourselves below with the username attached.
            expected_statuses=(400,),
            label=f"set NC password for {username}",
        )
        meta = _ocs_section(r, "meta")
        status = meta.get("statuscode", 0)
        if status in (100, 200):
            log.info(f"Reset NC password for {username}")
            return True
        log.error(
            f"Failed to reset NC password for {username}: OCS status {status} "
            f"msg={meta.get('message', '')!r} (HTTP {r.status_code} "
            f"body={_body_excerpt(r)})"
        )
        return False
    except Exception as e:
        log.error(f"Error resetting NC password for {username}: {e}", exc_info=True)
        return False


def create_nc_account(username, password, display_name, email, groups):
    """Create a Nextcloud user account via provisioning API.

    Returns NC_ACCOUNT_CREATED, NC_ACCOUNT_EXISTS, or None on failure.

    IDEMPOTENCY (added 2026-09-03): "user already exists" is a SUCCESS here --
    it means some earlier attempt already made the account, so onboarding
    should carry on rather than abort.

    This is the exact bug that stranded RCT John Garcia for 7 days / 1,942
    retries: his first attempt READ TIMED OUT at 30s *after* NC had already
    created the account. The old code called r.raise_for_status() first, so an
    "exists" reply (HTTP 400 / OCS 102) raised before the status code could
    ever be inspected, returned False, and aborted onboarding forever. Two
    fixes below: parse the OCS body instead of raising on HTTP status, and on
    an ambiguous transport failure ASK NC whether the user now exists.
    """
    # NC provisioning API needs form-encoded data with groups[] array
    form_data = {
        "userid": username,
        "password": password,
        "displayName": display_name,
        "email": email,
    }
    # requests handles multiple values for same key with a list of tuples
    form_pairs = list(form_data.items())
    for g in groups:
        form_pairs.append(("groups[]", g))

    try:
        r = nc_request(
            "POST", "/ocs/v2.php/cloud/users",
            data=form_pairs, user=NC_SVC_USER, passwd=NC_SVC_PASS,
            # HTTP 400 is how NC delivers OCS 102 "User already exists", which
            # is a SUCCESS for us. We inspect and log the body ourselves below.
            expected_statuses=(400,),
            label=f"create NC account {username}",
        )
    except Exception as e:
        # AMBIGUOUS failure (timeout / reset): the account may exist anyway.
        log.error(f"Error creating NC account {username}: {e}", exc_info=True)
        if nc_user_exists(username) is True:
            log.warning(
                f"NC account {username} exists despite the failed request "
                f"— treating as created (idempotent recovery)"
            )
            return NC_ACCOUNT_EXISTS
        return None

    # Parse the OCS body regardless of HTTP status: NC answers HTTP 400 with
    # OCS statuscode 102 for "User already exists".
    meta = _ocs_section(r, "meta")
    status = meta.get("statuscode", 0)
    msg = meta.get("message", "") or ""

    if status in (100, 200):
        log.info(f"Created NC account: {username}")
        return NC_ACCOUNT_CREATED
    if status == 102 or "already exists" in msg.lower():
        log.warning(f"NC account {username} already exists — continuing onboarding (idempotent)")
        return NC_ACCOUNT_EXISTS

    log.error(
        f"Failed to create NC account {username}: OCS status {status} "
        f"msg={msg!r} (HTTP {r.status_code} body={_body_excerpt(r)})"
    )
    return None


def add_to_nc_groups(username, groups):
    """Add a user to Nextcloud groups. Returns the list of groups that FAILED.

    Group membership is what actually grants a recruit access to shares and
    Talk rooms, so a per-group warning that no caller could see was a silent
    half-onboarding. The caller decides how loud to be.
    """
    failed = []
    for group in groups:
        try:
            nc_api(
                "POST",
                f"/ocs/v2.php/cloud/users/{quote(username)}/groups",
                json_data={"groupid": group},
                user=NC_SVC_USER,
                passwd=NC_SVC_PASS,
            )
            log.info(f"Added {username} to group: {group}")
        except Exception as e:
            log.warning(f"Failed to add {username} to group {group}: {e}")
            failed.append(group)
    return failed


def create_portal_member(info, nc_username, team):
    """Insert a member record into the Praetorium portal database."""
    try:
        import psycopg2

        # Parse name
        name = info.get("Legal Name", "")
        parts = name.split()
        first_name = parts[0] if parts else ""
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

        conn = psycopg2.connect(
            host=_resolve_portal_db_host(),
            port=PORTAL_DB_PORT,
            dbname=PORTAL_DB_NAME,
            user=PORTAL_DB_USER,
            password=PORTAL_DB_PASS,
        )
        cur = conn.cursor()

        # Get next serial number
        cur.execute("SELECT COALESCE(MAX(serial_seq), 0) + 1 FROM members")
        next_seq = cur.fetchone()[0]
        serial_number = f"XIII-{next_seq:04d}"

        # Parse address if available (format: "Street, City, State ZIP")
        raw_addr = info.get("Address", "")
        addr_parts = [p.strip() for p in raw_addr.split(",")]
        street = addr_parts[0] if len(addr_parts) >= 1 else ""
        city = addr_parts[1] if len(addr_parts) >= 2 else ""
        state_zip = addr_parts[2] if len(addr_parts) >= 3 else ""
        state_code = state_zip.split()[0] if state_zip else ""
        zip_code = state_zip.split()[1] if len(state_zip.split()) > 1 else ""

        is_vet = info.get("Veteran", "").lower() in ("yes", "true")

        # Geocode address for portal map
        geo_addr = f"{street}, {city}, TX {zip_code}" if street else (f"{city}, TX {zip_code}" if city else "")
        geo_lat, geo_lon = geocode_address(geo_addr) if geo_addr else (None, None)
        if geo_lat is None and city:
            # Fallback: geocode just city + zip
            geo_lat, geo_lon = geocode_address(f"{city}, TX {zip_code}")
        if geo_lat:
            log.info(f"Geocoded {first_name} {last_name}: {geo_lat:.4f}, {geo_lon:.4f}")
        else:
            log.warning(f"Could not geocode address for {first_name} {last_name}, map pin will be missing")
        has_ltc = info.get("LTC", "").lower() in ("yes", "true")

        # Proton email (unit comms) vs personal email (application email / fallback)
        proton_email = info.get("📧 Proton Mail", "").strip()
        application_email = info.get("Email", "")

        cur.execute("""
            INSERT INTO members (
                first_name, last_name, email, personal_email, phone,
                address, city, state, zip_code,
                latitude, longitude,
                rank_grade, status, team, company,
                nc_username, join_date, is_veteran, mos,
                has_ltc, serial_seq, serial_number,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                NOW(), NOW()
            )
            ON CONFLICT (nc_username) DO NOTHING
            RETURNING id
        """, (
            first_name, last_name,
            proton_email if proton_email else application_email,
            application_email,
            info.get("Phone", ""),
            street, city, state_code, zip_code,
            geo_lat, geo_lon,
            "E-1",  # Recruit
            "recruit",
            team,
            "13th Legion",
            nc_username,
            datetime.now().date(),
            is_vet,
            info.get("MOS", "") if is_vet else "",
            has_ltc,
            next_seq,
            serial_number,
        ))

        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if result:
            log.info(f"Created portal member: {first_name} {last_name} ({serial_number})")
            return True
        else:
            log.warning(f"Member {first_name} {last_name} may already exist in portal DB")
            return False

    except ImportError:
        log.warning("psycopg2 not available — skipping portal DB insert")
        return False
    except Exception as e:
        log.error(
            f"Failed to create portal member row for "
            f"{info.get('Legal Name', '?')!r} (nc user {nc_username}, "
            f"team {team}): {e}",
            exc_info=True,
        )
        return False


PORTAL_CONTAINER = os.environ.get("PORTAL_CONTAINER", "praetorium-app")


def _portal_geocode(address, city=None, state=None, zip_code=None):
    """Geocode by delegating to the PORTAL geocoder inside the app container.

    This is the single source of truth: it runs the portal's
    app.geo.geocode_member_fields (US Census Bureau primary, Nominatim +
    zip-centroid fallbacks), so the daemon and the portal always agree on
    placement. We shell into the container because the host's Python TLS
    fingerprint gets rejected by the Census WAF, while the container's does
    not (same egress IP). Handles apartment/unit addresses the old
    Nominatim-only daemon path failed on (see RCT Cook, 2026-07-15).
    Returns (lat, lon) or (None, None).
    """
    payload = json.dumps({"address": address, "city": city, "state": state, "zip": zip_code})
    code = (
        "import sys, json;"
        "from app.geo import geocode_member_fields;"
        "d=json.load(sys.stdin);"
        "lat,lon=geocode_member_fields(d.get('address'),d.get('city'),d.get('state'),d.get('zip'));"
        "print(json.dumps({'lat':lat,'lon':lon}))"
    )
    try:
        r = subprocess.run(
            ["docker", "exec", "-i", "-e", "PYTHONPATH=/app", PORTAL_CONTAINER, "python3", "-c", code],
            input=payload, capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            log.warning(f"Portal geocode container call failed (rc={r.returncode}): {r.stderr.strip()[:200]}")
            return None, None
        out = json.loads(r.stdout.strip().splitlines()[-1])
        return out.get("lat"), out.get("lon")
    except Exception as e:
        log.warning(f"Portal geocode delegation error for {address!r}: {e}")
        return None, None


def _nominatim_geocode(address):
    """Host-side Nominatim fallback (used only if the portal container is unreachable).

    Deliberately uses `requests` directly rather than nc_request(): this is
    OpenStreetMap, not Nextcloud, and must not be sent NC credentials or the
    OCS-APIRequest header.
    """
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": "13thLegion-Praetorium/1.0"},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning(
                f"Nominatim lookup for {address!r} -> HTTP {r.status_code} "
                f"body={_body_excerpt(r)}"
            )
            return None, None
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
        log.info(f"Nominatim returned no match for {address!r}")
    except Exception as e:
        log.warning(f"Nominatim fallback failed for {address!r}: {e}")
    return None, None


def geocode_address(address):
    """Geocode a US address using the portal geocoder (Census-first), with a
    host-side Nominatim fallback only if the container is unreachable.

    Matches the portal so daemon and portal agree on team placement.
    """
    lat, lon = _portal_geocode(address)
    if lat is not None:
        return lat, lon
    return _nominatim_geocode(address)


def add_map_pin(name, team, address):
    """DEPRECATED no-op. We use the bespoke portal map (/roster/map), NOT the
    Nextcloud Maps app (which is disabled). Member lat/lon is stored on the
    roster row by create_portal_member and rendered by the portal map.
    Kept as a no-op so callers don't break. Gated off 2026-07-02.

    The old NC Maps implementation used to sit below an unconditional
    `return True`; it was deleted 2026-09-03 rather than left as unreachable
    code masquerading as behaviour. `git show 2069b46` has it if ever needed.
    """
    return True


def remove_map_pin(name):
    """DEPRECATED no-op. NC Maps app is disabled; portal map is authoritative.
    Gated off 2026-07-02. Unreachable implementation deleted 2026-09-03."""
    return True


_SUBMISSION_ID_RE = re.compile(r"\*Submission ID:\*\s*(\d+)")


def parse_submission_id(card):
    """Forms submission id from a Deck card description, or None.

    create_deck_card() writes '*Submission ID:* {id}' into the description.
    Note the SINGLE asterisks -- parse_card_for_onboarding() only picks up
    '**Key:** value' lines, so it never sees this field.
    """
    m = _SUBMISSION_ID_RE.search(card.get("description") or "")
    return int(m.group(1)) if m else None


def archive_applicant_files(name, sub_id):
    """Archive ONE applicant's uploaded files into [S-1] Admin/Applications/{Name}/.

    Returns the number of files archived.

    Replaces move_applicant_files(), which had three separate defects and had
    therefore never once succeeded (20 'No Forms folder yet' log lines dating
    to 2026-03-07, zero successful moves, empty Applications/ folder):

      1. It built 'Forms/{form_title}' WITHOUT the '3 - ' form-id prefix, so
         the very first PROPFIND 404'd and it returned early every time.
      2. It iterated EVERY submission folder under the form root, using `name`
         only for the destination. Had the path ever resolved, the first
         onboarding would have swept all 39+ applicants' documents into one
         person's folder. It now addresses exactly one submission.
      3. It used Depth 1 on the submission folder, but Forms nests uploads
         inside a per-question subfolder, so it would have found zero files
         even with the path fixed. list_submission_files() uses Depth 3.

    Uses ARCHIVE_VERB (default COPY) so the originals stay in Forms storage.
    """
    if sub_id is None:
        log.warning(
            f"No submission id on the card for {name} — cannot archive documents. "
            f"(Card descriptions carry '*Submission ID:* N'; cards created "
            f"before that field existed have nothing to archive from.)"
        )
        return 0

    dest_folder = applications_folder_url(name)
    folder_name = applicant_folder_name(name)
    archived = 0

    try:
        file_hrefs = list_submission_files(sub_id, label=f" (archive for {name})")
        if file_hrefs is None:
            log.warning(
                f"Forms folder missing for submission #{sub_id} — nothing to "
                f"archive for {name} ({forms_submission_url(sub_id)})"
            )
            return 0
        if not file_hrefs:
            log.info(f"No uploaded files for {name} (submission #{sub_id})")
            return 0

        # 201 = created, 405 = already there. Both fine.
        nc_request("MKCOL", dest_folder, ocs=False, expected_statuses=(405,),
                   label=f"MKCOL Applications/{folder_name}")

        for fhref in file_hrefs:
            enc_filename = fhref.split("/")[-1]      # keep encoding for the URL
            filename = unquote(enc_filename)         # decode for humans
            r = nc_request(
                ARCHIVE_VERB, f"{NC_URL}{fhref}", ocs=False,
                headers={"Destination": f"{dest_folder}/{enc_filename}",
                         "Overwrite": "T"},
                label=f"{ARCHIVE_VERB} {filename} -> Applications/{folder_name}",
            )
            if r.status_code in (201, 204):
                archived += 1
                log.info(f"Archived: {filename} → Applications/{folder_name}/")
            # Failures already logged with body by nc_request.

        log.info(
            f"Archived {archived}/{len(file_hrefs)} file(s) for {name} "
            f"(submission #{sub_id}) to [S-1] Admin/Applications/{folder_name}/"
        )
        return archived

    except Exception as e:
        log.error(
            f"Failed to archive applicant files for {name} "
            f"(submission #{sub_id}): {e}",
            exc_info=True,
        )
        return archived


def move_card_to_stack(card_id, from_stack, to_stack):
    """Move a Deck card to a different stack.
    
    Uses GET+PUT to target stack (reorder endpoint silently fails cross-stack).
    """
    try:
        # Get current card data from source stack
        card = nc_api("GET", f"/index.php/apps/deck/api/v1.0/boards/{BOARD_ID}/stacks/{from_stack}/cards/{card_id}")
        # PUT to target stack with all required fields
        nc_api(
            "PUT",
            f"/index.php/apps/deck/api/v1.0/boards/{BOARD_ID}/stacks/{to_stack}/cards/{card_id}",
            json_data={
                "title": card.get("title", ""),
                "type": "plain",
                "order": 0,
                "description": card.get("description", ""),
                "duedate": card.get("duedate"),
                "owner": card.get("owner", {}).get("uid", NC_USER) if isinstance(card.get("owner"), dict) else card.get("owner", NC_USER),
            },
        )
        log.info(f"Moved card #{card_id} to stack {to_stack}")
        return True
    except Exception as e:
        log.error(
            f"Failed to move card #{card_id} from stack {from_stack} to "
            f"{to_stack}: {e}", exc_info=True,
        )
        return False


def send_welcome_email(recipient_email, name, nc_username, nc_password, team):
    """Send welcome email to new recruit via Proton Bridge SMTP (PP-019)."""

    # 2026 training calendar
    calendar_html = """
    <ul style="margin: 5px 0; padding-left: 20px;">
        <li><s>09–11 JAN — BTBLK-04</s></li>
        <li><s>06–08 FEB — BTBLK-01</s></li>
        <li><s>21 FEB — Urban Evasion Course</s></li>
        <li>13–15 MAR — BTBLK-02</li>
        <li>09–12 APR — MCFTX (Multi-Company)</li>
        <li>15–17 MAY — BTBLK-03</li>
        <li>12–14 JUN — BTBLK-04</li>
        <li>10–12 JUL — BTBLK-01</li>
        <li>08 AUG — Family Day</li>
        <li>11–13 SEP — BTBLK-02</li>
        <li>08–11 OCT — MCFTX (Multi-Company)</li>
        <li>13–15 NOV — BTBLK-03</li>
        <li>11–13 DEC — BTBLK-04</li>
    </ul>"""

    tradoc_html = """
    <table style="width: 100%; font-size: 13px; border-collapse: collapse; margin: 10px 0;">
        <tr style="background: #1a1a2e; color: #d4a537;">
            <td style="padding: 8px; font-weight: bold;">Block 01 — Theory &amp; Medical</td>
            <td style="padding: 8px; font-weight: bold;">Block 02 — Weapons Qualification</td>
        </tr>
        <tr>
            <td style="padding: 8px; vertical-align: top;">
                • Customs &amp; Courtesies<br>
                • Drill &amp; Ceremony<br>
                • Gear Review<br>
                • History &amp; Philosophy<br>
                • Medical
            </td>
            <td style="padding: 8px; vertical-align: top;">
                • Weapons Familiarization<br>
                • Basic Rifle Marksmanship<br>
                • Rifle Qualification<br>
                • Shooting Drills<br>
                • Use of Force
            </td>
        </tr>
        <tr style="background: #1a1a2e; color: #d4a537;">
            <td style="padding: 8px; font-weight: bold;">Block 03 — Supplemental Skills</td>
            <td style="padding: 8px; font-weight: bold;">Block 04 — Combat Fundamentals</td>
        </tr>
        <tr>
            <td style="padding: 8px; vertical-align: top;">
                • Comms<br>
                • Land Navigation<br>
                • Convoy
            </td>
            <td style="padding: 8px; vertical-align: top;">
                • Individual Movement Techniques<br>
                • Patrolling<br>
                • React to Contact<br>
                • Recon 101
            </td>
        </tr>
    </table>"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #1a1a2e; line-height: 1.6; max-width: 650px; margin: 0 auto;">

<div style="background: #1a1a2e; padding: 20px; text-align: center;">
    <table style="margin: 0 auto;" cellpadding="0" cellspacing="0"><tr>
        <td style="vertical-align: middle; padding-right: 15px;">
            <img src="https://13thlegion.org/assets/img/crest.png" alt="13th Legion" height="70" style="display: block;">
        </td>
        <td style="vertical-align: middle; text-align: center;">
            <h1 style="color: #d4a537; margin: 0; font-size: 28px;">13TH LEGION</h1>
            <p style="color: #ccc; margin: 5px 0 0;">Texas State Militia — Dallas / Fort Worth</p>
        </td>
        <td style="vertical-align: middle; padding-left: 15px;">
            <img src="https://13thlegion.org/assets/img/tsm-seal.png" alt="TSM" height="70" style="display: block;">
        </td>
    </tr></table>
</div>

<div style="padding: 20px;">
    <p>Welcome to the 13th Legion, {name}!</p>

    <p>Thank you for joining us. The Texas State Militia offers excellent training and service opportunities, and the 13th is proud to be DFW's unit. Aside from our monthly field training exercises we also offer optional fitness, charity, and training activities — great opportunities to meet your fellow members and build skills that matter.</p>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        🔹 Assignment
    </h3>
    <p>You've been assigned to <strong>{team} Team</strong>. Your team leader will reach out to you shortly. By now you should be invited to your team's Signal messaging group.</p>
    <p>You can view the 13th's overall chain of command <a href="https://coc.13thlegion.org/">here</a>.</p>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        🔹 Your Nextcloud Account
    </h3>
    <p>Nextcloud is our secure, self-hosted platform for files, calendar, chat, and more. Everything lives here.</p>
    <table style="margin: 10px 0; font-size: 14px;">
        <tr><td style="padding: 4px 10px 4px 0; font-weight: bold;">URL:</td>
            <td><a href="https://cloud.13thlegion.org">cloud.13thlegion.org</a></td></tr>
        <tr><td style="padding: 4px 10px 4px 0; font-weight: bold;">Username:</td>
            <td><code>{nc_username}</code></td></tr>
        <tr><td style="padding: 4px 10px 4px 0; font-weight: bold;">Temporary Password:</td>
            <td><code>{nc_password}</code></td></tr>
    </table>
    <p><strong>Change your password on first login.</strong> Then enable 2FA (TOTP) in your security settings — this is mandatory.</p>

    <p style="margin-top: 12px;"><strong>Download the apps:</strong></p>
    <ul>
        <li><strong>Nextcloud</strong> (files, calendar, contacts) — <a href="https://apps.apple.com/app/nextcloud/id1125420102">iOS</a> · <a href="https://play.google.com/store/apps/details?id=com.nextcloud.client">Android</a> · <a href="https://nextcloud.com/install/#install-clients">Desktop</a></li>
        <li><strong>Nextcloud Talk</strong> (chat &amp; calls) — <a href="https://apps.apple.com/app/nextcloud-talk/id1296825574">iOS</a> · <a href="https://play.google.com/store/apps/details?id=com.nextcloud.talk2">Android</a></li>
    </ul>
    <p>When logging in to the apps, enter <strong>cloud.13thlegion.org</strong> as the server address, then use your username and password above.</p>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        🔹 Unit Portal
    </h3>
    <p>Access the unit portal at <a href="https://portal.13thlegion.org">portal.13thlegion.org</a> — log in with the same Nextcloud account above. Your TRADOC checklist, training progress, certifications, and profile are all tracked there.</p>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        🔹 Communications
    </h3>
    <ul>
        <li><strong>Signal</strong> — Your team leader will add you to the team chat. Make sure you have Signal installed. <strong>Please stay in contact with your team leader on Signal. Failing to do so, outside of gross negligence or misconduct, is the only way to get removed from the unit.</strong></li>
        <li><strong>Nextcloud Talk</strong> — Unit-wide chat channels are in your Nextcloud account.</li>
    </ul>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        🔹 Training Calendar
    </h3>
    <p>Below is this year's training calendar. You can always view our <a href="https://cloud.13thlegion.org/apps/calendar">training and events calendar here</a>.</p>
    {calendar_html}

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        🔹 Basic Training (TRADOC)
    </h3>
    <p>Pay special attention to the TRADOC blocks below — these are the subjects you'll be trained and tested on to become a fully patched member. Your progress is tracked on the <a href="https://portal.13thlegion.org">unit portal</a>.</p>
    {tradoc_html}

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        🔹 Your First FTX
    </h3>
    <p><strong>Bring:</strong> Rifle, plate carrier, IFAK, water, boots, weather-appropriate clothing. Don't stress about gear — show up with what you have.</p>

    <h3 style="color: #d4a537; border-bottom: 2px solid #d4a537; padding-bottom: 5px;">
        🔹 Additional Resources
    </h3>
    <p>Your Nextcloud account includes a <strong>Recruit Packet</strong> folder in "13th Legion Shared" with all the documents you need to get started: code of conduct, bylaws, TRADOC checklist, medical card, uniform SOP, and more.</p>
    <p>The <a href="https://portal.13thlegion.org">unit portal</a> is your one-stop shop for everything else:</p>
    <ul>
        <li><a href="https://portal.13thlegion.org/library">Resource Library</a> — field manuals, SOPs, TSM state documents, and reference material</li>
        <li><a href="https://portal.13thlegion.org/coc">Chain of Command</a></li>
        <li><a href="https://portal.13thlegion.org/calendar">Training &amp; Events Calendar</a></li>
    </ul>
</div>

<div style="background: #1a1a2e; padding: 15px; text-align: center;">
    <p style="color: #d4a537; margin: 0; font-style: italic;">
        Nunquam Non Paratus — Never Not Ready
    </p>
    <p style="color: #888; margin: 5px 0 0; font-size: 12px;">
        13th Legion • Texas State Militia • 13thlegion.org
    </p>
</div>

</body>
</html>"""

    plain = f"""Welcome to the 13th Legion, {name}!

Thank you for joining us. The Texas State Militia offers excellent training and service opportunities, and the 13th is proud to be DFW's unit. Aside from our monthly field training exercises we also offer optional fitness, charity, and training activities — great opportunities to meet your fellow members and build skills that matter.

ASSIGNMENT
  You've been assigned to {team} Team. Your team leader will reach out shortly.
  Chain of Command: https://coc.13thlegion.org/

YOUR NEXTCLOUD ACCOUNT
  URL: https://cloud.13thlegion.org
  Username: {nc_username}
  Temporary Password: {nc_password}
  Change your password on first login. Enable 2FA (mandatory).

  Download the apps:
  - Nextcloud (files/calendar): iOS, Android, or Desktop — https://nextcloud.com/install/#install-clients
  - Nextcloud Talk (chat/calls): search "Nextcloud Talk" in your app store
  When logging in, enter cloud.13thlegion.org as the server address.

UNIT PORTAL
  https://portal.13thlegion.org — log in with your Nextcloud account.
  Your TRADOC checklist, training progress, and profile are tracked here.

COMMUNICATIONS
  - Signal: your TL will add you to the team chat. STAY IN CONTACT — failing
    to communicate (outside of gross negligence/misconduct) is the only way
    to get removed from the unit.
  - Nextcloud Talk: unit-wide chat channels in your NC account
2026 TRAINING CALENDAR
  13-15 MAR — BTBLK-02          09-12 APR — MCFTX
  15-17 MAY — BTBLK-03          12-14 JUN — BTBLK-04
  10-12 JUL — BTBLK-01          08 AUG — Family Day
  11-13 SEP — BTBLK-02          08-11 OCT — MCFTX
  13-15 NOV — BTBLK-03          11-13 DEC — BTBLK-04
  Full calendar: https://cloud.13thlegion.org/apps/calendar

BASIC TRAINING (TRADOC)
  Block 01 — Theory & Medical: Customs, Drill, Gear Review, History, Medical
  Block 02 — Weapons Qual: Familiarization, BRM, Qual, Drills, Use of Force
  Block 03 — Supplemental: Comms, Land Nav, Convoy
  Block 04 — Combat Fundamentals: IMT, Patrolling, React to Contact, Recon

YOUR FIRST FTX
  Bring: Rifle, plate carrier, IFAK, water, boots, weather-appropriate clothing.
  Don't stress about gear — show up with what you have.

ADDITIONAL RESOURCES
  Your NC account has a "Recruit Packet" folder with all key documents.
  The unit portal (https://portal.13thlegion.org) is your one-stop shop:
  - Resource Library: https://portal.13thlegion.org/library
  - Chain of Command: https://portal.13thlegion.org/coc
  - Training & Events Calendar: https://portal.13thlegion.org/calendar

Nunquam Non Paratus — Never Not Ready.
— 13th Legion, Texas State Militia
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Welcome to the 13th Legion — Texas State Militia"
    msg["From"] = SMTP_FROM
    msg["To"] = recipient_email
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        log.info(f"Sent welcome email to {recipient_email}")
        return True
    except Exception as e:
        log.error(f"Failed to send welcome email to {recipient_email}: {e}")
        return False


def onboard_member(card, state, dry_run=False):
    """Bounded-retry wrapper around _onboard_member.

    Without this, ANY card that can't be onboarded (missing email, missing
    Proton address, NC failure) is retried every poll forever. That is how one
    card produced 1,942 failed attempts over 7 days and filled the Nextcloud
    log with 'Failed addUser attempt' noise.

    After MAX_ONBOARD_ATTEMPTS the card is dead-lettered and skipped. To retry
    it, S1 fixes the underlying problem and removes the card id from
    'onboard_dead_letter' in state.json.
    """
    card_id = card.get("id")

    if card_id in state.get("onboarded_cards", []):
        return False
    if card_id in state.get("onboard_dead_letter", []):
        return False

    if _onboard_member(card, state, dry_run=dry_run):
        state.setdefault("onboard_failures", {}).pop(str(card_id), None)
        return True

    fails = state.setdefault("onboard_failures", {})
    key = str(card_id)
    fails[key] = fails.get(key, 0) + 1
    if fails[key] >= MAX_ONBOARD_ATTEMPTS:
        state.setdefault("onboard_dead_letter", []).append(card_id)
        log.error(
            f"DEAD-LETTER: card #{card_id} failed onboarding {fails[key]}x — giving up. "
            f"Fix the card, then remove {card_id} from 'onboard_dead_letter' in "
            f"state.json to retry."
        )
    else:
        log.warning(
            f"Onboarding attempt {fails[key]}/{MAX_ONBOARD_ATTEMPTS} failed for card #{card_id}"
        )
    return False


def _onboard_member(card, state, dry_run=False):
    """
    Full onboarding for an approved recruit:
    1. Parse card for member info
    2. Create NC account with appropriate groups
    3. Create portal DB record
    4. Send welcome email
    5. Move card to "Complete"
    """
    card_id = card.get("id")

    info = parse_card_for_onboarding(card)
    name = info.get("Legal Name", info["raw_title"].replace("📋 ", "").replace("✅ ", ""))
    email = info.get("Email", "")

    if not email:
        log.warning(f"No email in card #{card_id} ({name}), skipping")
        return False

    # Parse Proton Mail address from card description
    proton_email = info.get("📧 Proton Mail", "").strip()
    # Clean up placeholder text
    if not proton_email or "pending" in proton_email.lower() or proton_email.startswith("_"):
        log.warning(f"No Proton Mail address set for card #{card_id} ({name}) — cannot onboard. "
                     "S1 must edit the card and fill in the Proton Mail field before moving to Approved.")
        return False

    log.info(f"Onboarding: {name} (proton: {proton_email}, app email: {email})")

    # Generate NC username: firstname.lastname
    parts = name.lower().split()
    if len(parts) >= 2:
        nc_username = f"{parts[0]}.{parts[-1]}"
    else:
        nc_username = parts[0] if parts else "recruit"

    # Clean username (alphanumeric + dots only)
    nc_username = "".join(c for c in nc_username if c.isalnum() or c == ".")

    nc_password = generate_password()

    # Determine team from geographic zone (bearing-based).
    # If geocoding fails, leave team UNSET (None = geo pending) rather than
    # silently defaulting to Alpha. The portal re-geocodes (Census Bureau) on
    # its next pass and assign_zone places them correctly (team_locked stays f).
    team = None
    raw_addr = info.get("Address", "")
    if raw_addr:
        glat, glon = geocode_address(raw_addr)
        if glat is not None:
            team, bearing = geo_assign_team(glat, glon)
            log.info(f"Geo-assigned {name} to {team} (bearing {bearing:.1f}°)")
    if team is None:
        log.warning(f"Geocode failed for {name}; leaving team UNSET (geo pending), portal will assign on re-geocode")

    # Build group list (only add Team-<X> group when team is known)
    groups = list(RECRUIT_GROUPS)
    if team:
        groups.append(f"Team-{team}")

    if dry_run:
        log.info(f"[DRY RUN] Would onboard: {name}")
        log.info(f"  NC user: {nc_username}, team: {team}, groups: {groups}")
        log.info(f"  Email: {email}")
        state.setdefault("onboarded_cards", []).append(card_id)
        return True

    # 1. Create NC account (use Proton Mail as the account email)
    parts = name.split()
    last_name = parts[-1] if len(parts) > 1 else name
    if last_name.lower() in ("jr", "jr.", "sr", "sr.", "ii", "iii", "iv") and len(parts) > 2:
        last_name = parts[-2]
    
    display_name = f"RCT {last_name}"
    nc_result = create_nc_account(nc_username, nc_password, display_name, proton_email, groups)
    if nc_result is None:
        log.error(f"Onboarding aborted for {name} — NC account creation failed")
        return False

    send_welcome = True
    if nc_result == NC_ACCOUNT_EXISTS:
        # Recovering a partially-completed onboarding. The account exists but we
        # do NOT know its password, so the freshly generated one in
        # nc_password would be wrong in the welcome email. Reset it -- but ONLY
        # if they have never logged in, otherwise we'd clobber a password the
        # member chose themselves.
        never_logged_in = nc_user_never_logged_in(nc_username)
        if never_logged_in is True:
            if not set_nc_password(nc_username, nc_password):
                log.error(
                    f"Onboarding aborted for {name} — account exists but password "
                    f"reset failed, so we cannot send working credentials"
                )
                return False
            # Groups may also be incomplete from the partial run.
            failed_groups = add_to_nc_groups(nc_username, groups)
            if failed_groups:
                # Not fatal to onboarding, but the recruit will be missing
                # share/Talk access until someone fixes it, so say so loudly.
                log.error(
                    f"{nc_username} could not be added to {failed_groups} — "
                    f"recruit will lack access until this is corrected"
                )
                notify_s1_nctalk(
                    f"⚠️ **Onboarding: group assignment failed**\n"
                    f"Account: `{nc_username}` ({name})\n"
                    f"Failed groups: `{', '.join(failed_groups)}`\n"
                    f"Add them manually in Nextcloud user admin."
                )
        else:
            # Either they've logged in, or we couldn't tell. Both mean "don't
            # touch the password and don't mail credentials".
            send_welcome = False
            log.warning(
                f"{nc_username} already exists and has logged in (or lastLogin "
                f"unreadable) — skipping password reset and welcome email; "
                f"completing the rest of onboarding"
            )

    # 2. Create portal DB record.
    # DELIBERATELY non-fatal: the NC account already exists at this point, and
    # the portal re-syncs rosters on its own pass, so aborting here would
    # burn onboarding attempts toward the dead-letter cap for a transient DB
    # blip. But it must never be silent -- a recruit with credentials and no
    # roster row is invisible to S1.
    if not create_portal_member(info, nc_username, team):
        log.error(
            f"Portal roster row NOT created for {name} ({nc_username}) — "
            f"continuing onboarding; the member exists in Nextcloud but not "
            f"on the portal roster"
        )
        notify_s1_nctalk(
            f"⚠️ **Onboarding: portal roster insert failed**\n"
            f"Member: {name} (`{nc_username}`, team {team})\n"
            f"Their Nextcloud account was created, but the Praetorium roster "
            f"row was not. Add them manually or re-run the portal sync."
        )

    # 3. Add to NC Maps
    raw_addr = info.get("Address", "")
    if raw_addr and team:
        # Build full address string for geocoding
        city = info.get("City", "")
        map_addr = f"{raw_addr}, {city}, TX" if city else raw_addr
        last_first = f"{name.split()[-1]}, {name.split()[0]}" if " " in name else name
        add_map_pin(last_first, team, map_addr)

    # 4. Archive applicant documents into the S1 recruiting folder.
    # Needs the submission id so we only touch THIS applicant's files.
    archive_applicant_files(name, parse_submission_id(card))

    # 5. Send welcome email (to Proton Mail address).
    # Guarded so a re-run can never send a second copy.
    if card_id in state.get("welcome_emailed_cards", []):
        log.info(f"Welcome email already sent for card #{card_id} — not resending")
    elif send_welcome:
        send_welcome_email(proton_email, name, nc_username, nc_password, team)
        state.setdefault("welcome_emailed_cards", []).append(card_id)

    # 6. Move card to Complete
    move_card_to_stack(card_id, STACKS["approved"], STACKS["complete"])

    # 7. Update card title to show completion
    # IMPORTANT: Deck API PUT replaces ALL fields — always include description
    try:
        updated_desc = card.get("description", "") + f"\n\n---\n*Onboarded: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n*NC Account: {nc_username}*\n*Team: {team}*"
        nc_api(
            "PUT",
            f"/index.php/apps/deck/api/v1.0/boards/{BOARD_ID}/stacks/{STACKS['complete']}/cards/{card_id}",
            json_data={
                "title": f"✅ {name}",
                "type": "plain",
                "order": card.get("order", 0),
                "description": updated_desc,
                "duedate": card.get("duedate"),
                "owner": card.get("owner", {}).get("uid", NC_USER) if isinstance(card.get("owner"), dict) else card.get("owner", NC_USER),
            },
        )
    except Exception as e:
        log.warning(f"Failed to update card title for #{card_id}: {e}")

    state.setdefault("onboarded_cards", []).append(card_id)
    log.info(f"Onboarding complete: {name} → {nc_username} → Team {team}")
    return True


def check_approved_cards(state, dry_run=False):
    """Check for cards in Approved stack and trigger onboarding."""
    cards = get_stack_cards(STACKS["approved"])
    onboarded = 0

    for card in cards:
        if onboard_member(card, state, dry_run=dry_run):
            onboarded += 1

    return onboarded


# ─── Main Loop ───────────────────────────────────────────────────────────────


def send_payment_email(recipient_email, name):
    """Send the Documents & Payment email."""
    subject = "13th Legion Application — Next Steps: Documents & Payment"
    body_text = f"""\
{name},

Your application to the 13th Legion has been advanced to the final onboarding phase.

To complete your background check and finalize your membership, please submit the ONE TIME, NON-REFUNDABLE $50 membership fee. This covers the cost of your criminal background check.

Payment can be made via credit/debit card or PayPal at:
https://portal.13thlegion.org/apply/fee?email={recipient_email}/

If you have any questions, please reply directly to this email or reach out to your recruiter.

Respectfully,

S1 Recruiting
13th Legion, Texas State Militia
"""
    body_html = f"""\
<html><body>
<p>{name},</p>
<p>Your application to the 13th Legion has been advanced to the final onboarding phase.</p>
<p>To complete your background check and finalize your membership, please submit the <strong>ONE TIME, NON-REFUNDABLE $50 membership fee</strong>. This fee covers the cost of your criminal background check.</p>
<p>Payment can be made securely via credit/debit card or PayPal at: <br>
<a href="https://portal.13thlegion.org/apply/fee?email={recipient_email}">https://portal.13thlegion.org/apply/fee?email={recipient_email}</a></p>
<p>If you have any questions, please reply directly to this email or reach out to your recruiter.</p>
<p>Respectfully,<br><br>
<strong>S1 Recruiting</strong><br>
13th Legion, Texas State Militia</p>
</body></html>
"""
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = recipient_email

    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            if SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        log.info(f"Payment email sent successfully to {recipient_email}")
        return True
    except Exception as e:
        log.error(f"Failed to send payment email to {recipient_email}: {e}")
        return False

def check_payment_cards(state, dry_run=False):
    """Check for cards in Documents & Payment stack and trigger payment email."""
    cards = get_stack_cards(STACKS["documents_payment"])
    emailed = 0
    
    for card in cards:
        card_id = card.get("id")
        if card_id in state.get("payment_emailed_cards", []):
            continue
            
        info = parse_card_for_onboarding(card)
        name = info.get("Legal Name", info["raw_title"].replace("📋 ", "").replace("✅ ", ""))
        proton_email = info.get("📧 Proton Mail", "").strip()
        
        # Determine the recipient: new protonmail or application email
        if proton_email and "pending" not in proton_email.lower() and not proton_email.startswith("_"):
            target_email = proton_email
        else:
            target_email = info.get("Email", "")
            
        if not target_email:
            log.warning(f"No email found for card #{card_id} ({name}) — cannot send payment instructions.")
            continue

        # Guard against malformed recipients (e.g. a Y/N field value like "No"
        # getting parsed into the email slot). A bad address is a PERMANENT
        # failure — mark the card handled so we don't loop-spam SMTP errors
        # every poll cycle, and flag S1 to fix the card data.
        _te = target_email.strip()
        if ("@" not in _te) or ("." not in _te.split("@")[-1]) or (" " in _te):
            log.error(
                f"Invalid payment-email recipient for card #{card_id} ({name}): "
                f"{target_email!r} — skipping permanently. S1 should fix the card."
            )
            try:
                notify_s1_nctalk(
                    f"⚠️ **Payment email skipped — bad address**\n"
                    f"Card #{card_id} ({name}) has an invalid email: `{target_email}`.\n"
                    f"Fix the Proton/application email on the card, then move it out "
                    f"and back into *Documents & Payment* to re-trigger."
                )
            except Exception as _ne:
                log.warning(f"Could not notify S1 about bad email on card #{card_id}: {_ne}")
            state.setdefault("payment_emailed_cards", []).append(card_id)
            continue

        log.info(f"Sending payment email for {name} to {target_email}")
        
        if dry_run:
            log.info(f"[DRY RUN] Would send payment email to: {target_email}")
            state.setdefault("payment_emailed_cards", []).append(card_id)
            emailed += 1
            continue
            
        if send_payment_email(target_email, name):
            state.setdefault("payment_emailed_cards", []).append(card_id)
            emailed += 1
            
    return emailed

def main():
    parser = argparse.ArgumentParser(description="13th Legion S1 Recruit Pipeline Daemon")
    parser.add_argument("--poll-interval", type=int, default=300, help="Seconds between checks (default 5 min)")
    parser.add_argument("--dry-run", action="store_true", help="Don't create accounts or send emails")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--test-email", metavar="EMAIL", help="Send a test welcome email and exit")
    args = parser.parse_args()

    # Test email mode
    if args.test_email:
        log.info(f"Sending test welcome email to {args.test_email}")
        send_welcome_email(
            args.test_email,
            name="Test Recruit",
            nc_username="test.recruit",
            nc_password="TestP@ssw0rd123!",
            team="Arrow",
        )
        return

    log.info("=" * 60)
    log.info("Recruit Pipeline Daemon starting")
    log.info(f"Poll interval: {args.poll_interval}s | Dry run: {args.dry_run}")
    log.info("=" * 60)
    sd_notify("READY=1")

    state = load_state()

    while True:
        try:
            # Phase 1: Check for new form submissions → create Deck cards
            new = check_new_submissions(state, dry_run=args.dry_run)
            if new:
                log.info(f"Processed {new} new submission(s)")

            # Phase 1.5: Check for cards in 'Documents & Payment' → send payment email
            emailed = check_payment_cards(state, dry_run=args.dry_run)
            if emailed:
                log.info(f"Emailed payment instructions to {emailed} applicant(s)")

            # Phase 2: Check for approved cards → onboard
            onboarded = check_approved_cards(state, dry_run=args.dry_run)
            if onboarded:
                log.info(f"Onboarded {onboarded} new member(s)")

            state["last_check"] = int(time.time())
            save_state(state)

            # Heartbeat to Uptime Kuma.
            # JUSTIFIED SILENCE (mostly): the heartbeat is monitoring, not
            # pipeline work, and Kuma being unreachable must never fail a poll
            # cycle. But it is logged at debug rather than swallowed outright,
            # so "why did Kuma go red" is answerable from the daemon log.
            try:
                kuma_msg = urllib.parse.quote(f"OK: {new or 0} new, {onboarded or 0} onboarded")
                kuma_req = urllib.request.Request(KUMA_PUSH_URL + kuma_msg, headers={"User-Agent": "recruit-daemon/1.0"})
                urllib.request.urlopen(kuma_req, timeout=10)
            except Exception as e:
                log.debug(f"Uptime Kuma heartbeat failed (non-fatal): {e}")

        except Exception as e:
            log.error(f"Error in main loop: {e}", exc_info=True)
        finally:
            # Systemd watchdog ping — ALWAYS, even when a phase raised.
            # This used to sit inside the try, after the phases, so any
            # exception skipped it. With poll=300s and WatchdogSec=15min, three
            # consecutive transient failures (e.g. NC 502 on the Forms API) were
            # enough for systemd to SIGABRT a perfectly healthy daemon. That
            # happened twice on 2026-09-02. The process is alive and looping
            # here regardless of whether a phase succeeded, so say so.
            sd_notify("WATCHDOG=1")

        if args.once:
            break

        time.sleep(args.poll_interval)

    log.info("Daemon stopped")


if __name__ == "__main__":
    main()

