"""Tesserarius — activity-policy nag bot.

Runs on a timer (systemd). For each patched, non-leave member, measures
days-since-contact and days-since-FTX against the unit's configured activity
policy, and at each threshold crossing sends a Talk DM + email to BOTH the
member and their team leader. Idempotent per tier (state file), re-nags every
RENAG_DAYS while a member stays in the same tier.

Reads config from the portal DB (app_settings) and reuses the recruit-daemon's
DB/Talk/SMTP primitives, so it lives beside /opt/recruit-pipeline/recruit-daemon.py.
"""
import importlib.util
import json
import logging
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tesserarius.nag")

# ── Load the recruit daemon's primitives (DB host, Talk API, SMTP creds) ──────
_DAEMON = "/opt/recruit-pipeline/recruit-daemon.py"
spec = importlib.util.spec_from_file_location("recruit_daemon", _DAEMON)
_d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_d)

resolve_db_host = _d._resolve_portal_db_host
DB_PORT = _d.PORTAL_DB_PORT
DB_NAME = _d.PORTAL_DB_NAME
DB_USER = _d.PORTAL_DB_USER
DB_PASS = _d.PORTAL_DB_PASS
NC_URL = _d.NC_URL
SMTP_HOST = _d.SMTP_HOST
SMTP_PORT = _d.SMTP_PORT
SMTP_USER = _d.SMTP_USER
SMTP_PASS = _d.SMTP_PASS
SMTP_FROM = _d.SMTP_FROM
# Tesserarius posts to Talk as itself (bot account).
NC_TALK_USER = os.environ.get("NC_TALK_USER", "tesserarius")
NC_TALK_PASS = os.environ.get("NC_TALK_PASS", "")

STATE_FILE = Path(os.environ.get("TESSERARIUS_STATE_FILE", "/opt/recruit-pipeline/tesserarius_nag_state.json"))
RENAG_DAYS = int(os.environ.get("TESSERARIUS_RENAG_DAYS", "14"))

# ── Defaults (overridden by app_settings) ─────────────────────────────────────
DEFAULTS = {
    "no_contact_outreach_weeks": 6,
    "no_contact_response_weeks": 2,
    "no_ftx_verbal_months": 2,
    "no_ftx_written_months": 3,
    "no_ftx_removal_months": 4,
    "loa_months": 6,
}


def _db():
    import psycopg2
    return psycopg2.connect(
        host=resolve_db_host(), port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )


def load_policy() -> dict:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM app_settings WHERE key LIKE 'activity_%'")
    rows = dict(cur.fetchall())
    cur.close()
    conn.close()
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        v = rows.get("activity_" + k)
        if v is not None:
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                pass
    out["no_contact_removal_weeks"] = out["no_contact_outreach_weeks"] + out["no_contact_response_weeks"]
    return out


def load_members() -> list[dict]:
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, nc_username, first_name, last_name, email, team,
               leadership_title, last_login, last_ftx, on_leave, leave_end
        FROM members
        WHERE status = 'active'
        ORDER BY team, last_name
        """
    )
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def talk_post(token, message):
    import requests
    r = requests.post(
        f"{NC_URL}/ocs/v2.php/apps/spreed/api/v1/chat/{token}",
        auth=(NC_TALK_USER, NC_TALK_PASS),
        headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        data={"message": message},
        timeout=20,
    )
    return r.status_code == 201


def talk_dm_token(user_id):
    """Create (or fetch) the 1:1 Tesserarius<->user room and return its token."""
    import requests
    r = requests.post(
        f"{NC_URL}/ocs/v2.php/apps/spreed/api/v1/room",
        auth=(NC_TALK_USER, NC_TALK_PASS),
        headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        data={"roomType": "1", "invite": user_id},
        timeout=20,
    )
    try:
        data = r.json().get("ocs", {}).get("data", {})
        return data.get("token")
    except Exception:
        return None


def send_email(to, subject, body_html):
    if not to or "@" not in to:
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg.attach(MIMEText(body_html, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        log.info("emailed %s", to)
    except Exception as e:
        log.error("email to %s failed: %s", to, e)


def days_since(dt):
    if dt is None:
        return None
    return (datetime.utcnow() - dt).days


def tier_for(m, p):
    """Return (tier, urgency_label) or None. Higher tier wins."""
    # No-contact
    c = days_since(m["last_login"])
    if m["last_ftx"] and (c is None or m["last_ftx"] > m["last_login"]):
        c = days_since(m["last_ftx"])
    if c is None:
        c = 9999
    removal_w = p["no_contact_removal_weeks"]
    outreach_w = p["no_contact_outreach_weeks"]
    ftx = days_since(m["last_ftx"])
    if ftx is None:
        ftx = 9999

    # Most-severe single message: prefer removal, then written, verbal, then contact.
    tiers = []
    if c >= removal_w * 7:
        tiers.append(("no_contact_removal", f"{removal_w} weeks with no contact"))
    elif c >= outreach_w * 7:
        tiers.append(("no_contact_outreach", f"{outreach_w} weeks with no contact"))
    if ftx >= p["no_ftx_removal_months"] * 30:
        tiers.append(("no_ftx_removal", f"{p['no_ftx_removal_months']} months with no FTX"))
    elif ftx >= p["no_ftx_written_months"] * 30:
        tiers.append(("no_ftx_written", f"{p['no_ftx_written_months']} months with no FTX"))
    elif ftx >= p["no_ftx_verbal_months"] * 30:
        tiers.append(("no_ftx_verbal", f"{p['no_ftx_verbal_months']} months with no FTX"))

    if not tiers:
        return None
    # Removal is the most severe; else the first (highest) FTX tier.
    if "no_contact_removal" in [t[0] for t in tiers] or "no_ftx_removal" in [t[0] for t in tiers]:
        return next(t for t in tiers if t[0].endswith("_removal"))
    return tiers[0]


def tier_message(m, tier, label):
    name = f"{m['first_name']} {m['last_name']}".strip()
    if tier == "no_contact_removal":
        return (f"⚠️ **Activity Policy — removal warning**\n\n{name}, you've had {label}. "
                "Please contact your team leader immediately to avoid removal from the company.")
    if tier == "no_contact_outreach":
        return (f"👋 **Check-in**\n\n{name}, it's been {label}. Please reach out to your "
                "team leader to stay current.")
    if tier == "no_ftx_removal":
        return (f"⚠️ **Activity Policy — removal warning**\n\n{name}, you've gone {label}. "
                "Attend the next FTX or contact Command immediately.")
    if tier == "no_ftx_written":
        return (f"📝 **Written warning**\n\n{name}, you've missed {label}. Attendance at the "
                "next FTX is required.")
    return (f"🗣️ **Verbal warning**\n\n{name}, you've missed {label}. Please make the next FTX.")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    dry_run = "--dry-run" in sys.argv
    p = load_policy()
    members = load_members()
    state = load_state()
    leaders = {m["team"]: m for m in members if m["leadership_title"] == "Team Leader"}

    now = datetime.utcnow()
    acted = 0
    for m in members:
        if m["on_leave"]:
            continue
        if m["leadership_title"] == "Team Leader":
            continue  # leaders are not nagged (they're the other end of it)
        tier = tier_for(m, p)
        if tier is None:
            continue
        key = f"{m['id']}:{tier[0]}"
        last = state.get(key)
        last_dt = datetime.fromisoformat(last) if last else None
        if last_dt and (now - last_dt).days < RENAG_DAYS:
            continue  # already nagged recently for this tier

        leader = leaders.get(m["team"])
        subject = f"13th Legion — Activity Policy notice for {m['first_name']} {m['last_name']}"
        body = tier_message(m, tier[0], tier[1])

        if dry_run:
            log.info("[dry-run] %s -> %s", key, tier[0])
            continue

        # DM the member (via 1:1) and email the member + team leader.
        if m["nc_username"]:
            token = talk_dm_token(m["nc_username"])
            if token:
                talk_post(token, body)
        send_email(m["email"], subject, body.replace("\n", "<br>"))
        if leader:
            lead_msg = (
                f"🔔 **Activity Policy — team member flagged**\n\n"
                f"{m['first_name']} {m['last_name']} ({m['team'] or 'no team'}): {tier[1]}. "
                f"Please reach out."
            )
            if leader["nc_username"]:
                ltoken = talk_dm_token(leader["nc_username"])
                if ltoken:
                    talk_post(ltoken, lead_msg)
            send_email(leader["email"], f"13th Legion — {m['first_name']} {m['last_name']} activity", lead_msg.replace("\n", "<br>"))

        state[key] = now.isoformat()
        acted += 1

    save_state(state)
    log.info("nag pass complete: %d actions", acted)


if __name__ == "__main__":
    main()
