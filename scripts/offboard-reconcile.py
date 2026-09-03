#!/usr/bin/env python3
"""Reconcile separated 13th Legion members against their real Nextcloud state.

WHY THIS EXISTS ON THE HOST
The portal (praetorium-app) has no docker socket and no docker binary, so it
cannot run `occ`. Two offboarding steps therefore CANNOT be done from the app:

  * Talk eviction  -- Talk's API only lets you list rooms for the *authenticated*
                      user, so an admin cannot enumerate someone else's rooms.
                      `occ talk:user:remove` is the only practical primitive.
  * Token revocation -- there is no admin OCS endpoint for another user's device
                      tokens; it needs `occ user:auth-tokens:*` or the NC DB.

Disabling an NC account does NOT evict Talk rooms and does NOT revoke device
tokens. A disabled account holding a live token authenticates far enough to hit
DisabledUserException and return HTTP 503, forever. RCT Rankin did exactly that
every ~20 minutes for two months (separated 2026-06-29, found 2026-09-03) and
those were the only 5xx errors on the server.

WHAT IT DOES
For every portal member with status in ('separated','blacklisted') and an
nc_username, check for leftovers and clean them:
  1. NC group memberships   -> occ group:removeuser
  2. NC Talk room attendees -> occ talk:user:remove
  3. NC device auth tokens  -> delete oc_authtoken + matching
                               oc_oauth2_access_tokens rows (no FK/cascade
                               between them, so both must be handled or the
                               oauth2 rows orphan)
Then flip the corresponding separation_log flags so the portal dashboard shows
cleanup as complete. Alerts Discord only when it actually changed something or
hit an error -- same server-side pattern as spooky-bot-audit (no model in the
loop).

Idempotent and safe to run on a timer. --dry-run reports without changing
anything. Never touches the separation record itself (reason, date,
initiated_by) and never disables/enables an account.
"""
import argparse
import json
import logging
import re
import subprocess
import sys
import urllib.request

# nc_usernames are generated as firstname.lastname (alnum + dots). Anything
# outside that is refused rather than interpolated into SQL.
SAFE_UID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

LOG_PATH = "/var/log/offboard-reconcile.log"
DISCORD_CHANNEL = "1466732342704996352"  # Cav DM
DISCORD_USER = "179481162710908928"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("offboard-reconcile")


def sh(args, timeout=60):
    """Run a command, return (rc, stdout, stderr)."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def container_env(container, key):
    rc, out, _ = sh(["docker", "inspect", container,
                     "--format", "{{range .Config.Env}}{{println .}}{{end}}"])
    if rc != 0:
        return ""
    for line in out.splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""


def occ(*args, timeout=60):
    return sh(["docker", "exec", "-u", "www-data", "nextcloud-app", "php", "occ", *args],
              timeout=timeout)


def pg(sql):
    """Query the portal Postgres. Returns list of row-tuples (tab-separated)."""
    pw = container_env("praetorium-db", "POSTGRES_PASSWORD")
    if not pw:
        raise RuntimeError("could not resolve POSTGRES_PASSWORD from praetorium-db")
    rc, out, err = sh(["docker", "exec", "-e", f"PGPASSWORD={pw}", "-i", "praetorium-db",
                       "psql", "-U", "praetorium", "-d", "praetorium",
                       "-t", "-A", "-F", "\t", "-c", sql])
    if rc != 0:
        raise RuntimeError(f"psql failed: {err}")
    return [line.split("\t") for line in out.splitlines() if line.strip()]


def ncdb(sql):
    """Query/modify the Nextcloud MariaDB."""
    pw = (container_env("nextcloud-db", "MYSQL_PASSWORD")
          or container_env("nextcloud-db", "MARIADB_PASSWORD"))
    if not pw:
        raise RuntimeError("could not resolve DB password from nextcloud-db")
    rc, out, err = sh(["docker", "exec", "-i", "nextcloud-db", "mariadb",
                       "-unextcloud", f"-p{pw}", "nextcloud", "-N", "-B", "-e", sql])
    if rc != 0:
        raise RuntimeError(f"mariadb failed: {err}")
    return [line.split("\t") for line in out.splitlines() if line.strip()]


def nc_user(uid):
    """Parsed `occ user:info` for uid, or None when the account does not exist."""
    rc, out, _ = occ("user:info", uid, "--output=json")
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def nc_groups(uid):
    info = nc_user(uid)
    return None if info is None else (info.get("groups", []) or [])


def nc_is_neutralised(uid):
    """True when the account cannot be logged into: absent, or explicitly disabled.

    Used to reconcile separation_log.nc_account_disabled against reality. Three
    separations sat permanently 'unresolved' because the flag was false while
    the NC account had never existed at all -- there was nothing to disable, so
    the panel nagged forever. Absent counts as neutralised.
    """
    info = nc_user(uid)
    if info is None:
        return True
    return info.get("enabled") is False


# Reactivation audit rows live in separation_log too (reason like
# "reactivated (inactive -> active)"). Never treat those as offboardings.
NOT_REACTIVATION = "reason NOT ILIKE 'reactivated%%'"


AUDIT_SCRIPT = "/usr/local/sbin/spooky-bot-audit.sh"


def discord_token():
    """Reuse the token the existing server-side audit script already holds,
    rather than storing a second copy of the same secret on this box."""
    try:
        with open(AUDIT_SCRIPT, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DISCORD_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except Exception as e:
        log.warning(f"could not read Discord token from {AUDIT_SCRIPT}: {e}")
    return ""


def notify_discord(text):
    token = discord_token()
    if not token:
        log.warning("no Discord token available; skipping alert")
        return False
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL}/messages",
        data=json.dumps({"content": text[:1900]}).encode(),
        headers={"Authorization": f"Bot {token}",
                 "Content-Type": "application/json",
                 "User-Agent": "offboard-reconcile/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status in (200, 201)
    except Exception as e:
        log.warning(f"Discord alert failed: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="suppress Discord alert")
    args = ap.parse_args()

    rows = pg("""
        SELECT m.id, m.nc_username, m.first_name, m.last_name, m.status
          FROM members m
         WHERE m.status IN ('separated','blacklisted')
           AND m.nc_username IS NOT NULL AND m.nc_username <> ''
         ORDER BY m.id;
    """)
    log.info(f"checking {len(rows)} separated/blacklisted member(s)")

    fixed_summary = []
    errors = []

    for mid, uid, first, last, status in rows:
        who = f"{first} {last} ({uid})"
        if not SAFE_UID.match(uid or ""):
            log.error(f"{who}: refusing unsafe nc_username {uid!r}")
            errors.append(f"{who}: unsafe nc_username, skipped")
            continue
        try:
            groups = nc_groups(uid)
            if groups is None:
                # No NC account at all -> nothing to clean; mark done.
                log.info(f"{who}: no NC account, marking cleanup complete")
                groups = []

            tal = ncdb(f"SELECT count(*) FROM oc_talk_attendees WHERE actor_id='{uid}';")
            n_talk = int(tal[0][0]) if tal else 0
            tok = ncdb(f"SELECT count(*) FROM oc_authtoken WHERE uid='{uid}';")
            n_tok = int(tok[0][0]) if tok else 0

            if not groups and n_talk == 0 and n_tok == 0:
                # Already clean -- just make sure the log reflects reality.
                if not args.dry_run:
                    dis = "true" if nc_is_neutralised(uid) else "false"
                    pg(f"""UPDATE separation_log
                              SET groups_removed=true, talk_removed=true,
                                  tokens_revoked=true, nc_account_disabled={dis}
                            WHERE member_id={int(mid)} AND {NOT_REACTIVATION};""")
                continue

            log.warning(f"{who}: LEFTOVERS groups={len(groups)} talk_rooms={n_talk} tokens={n_tok}")
            if args.dry_run:
                fixed_summary.append(f"[dry-run] {who}: {len(groups)} groups, {n_talk} rooms, {n_tok} tokens")
                continue

            # 1. groups
            for g in groups:
                rc, _, err = occ("group:removeuser", g, uid)
                if rc != 0:
                    errors.append(f"{who}: group '{g}' removal failed: {err}")
                else:
                    log.info(f"{who}: removed from group '{g}'")

            # 2. Talk rooms
            if n_talk:
                rc, _, err = occ("talk:user:remove", "--user", uid)
                if rc != 0:
                    errors.append(f"{who}: talk:user:remove failed: {err}")
                else:
                    log.info(f"{who}: evicted from {n_talk} Talk room(s)")

            # 3. auth tokens (both tables -- no FK/cascade between them)
            if n_tok:
                ncdb(f"""DELETE a FROM oc_oauth2_access_tokens a
                           JOIN oc_authtoken t ON t.id=a.token_id WHERE t.uid='{uid}';
                         DELETE FROM oc_authtoken WHERE uid='{uid}';""")
                log.info(f"{who}: revoked {n_tok} device token(s)")

            # verify + record
            g2 = nc_groups(uid) or []
            t2 = int(ncdb(f"SELECT count(*) FROM oc_talk_attendees WHERE actor_id='{uid}';")[0][0])
            k2 = int(ncdb(f"SELECT count(*) FROM oc_authtoken WHERE uid='{uid}';")[0][0])
            pg(f"""UPDATE separation_log
                      SET groups_removed={'true' if not g2 else 'false'},
                          talk_removed={'true' if t2 == 0 else 'false'},
                          tokens_revoked={'true' if k2 == 0 else 'false'},
                          nc_account_disabled={'true' if nc_is_neutralised(uid) else 'false'}
                    WHERE member_id={int(mid)} AND {NOT_REACTIVATION};""")

            if g2 or t2 or k2:
                errors.append(f"{who}: STILL dirty after cleanup (groups={len(g2)} rooms={t2} tokens={k2})")
            else:
                fixed_summary.append(f"{who}: cleaned {len(groups)} groups, {n_talk} rooms, {n_tok} tokens")

        except Exception as e:
            log.error(f"{who}: reconcile error: {e}")
            errors.append(f"{who}: {e}")

    if not fixed_summary and not errors:
        log.info("all separated members clean; nothing to do")
        return 0

    lines = []
    if fixed_summary:
        lines.append("**Offboard reconcile — cleaned up leftovers**")
        lines += [f"• {s}" for s in fixed_summary]
    if errors:
        lines.append("**⚠️ Offboard reconcile errors**")
        lines += [f"• {e}" for e in errors]
    msg = "\n".join(lines)
    log.info(msg.replace("\n", " | "))
    if not args.quiet and not args.dry_run:
        notify_discord(msg)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
