#!/bin/bash
# Spooky Talk-bot room-scope guard.
# Enforces: bot id 2 attached to EXACTLY the allowlist rooms. Removes drift.
# Writes a drift report to /var/log/spooky-bot-audit.log and, on drift,
# alerts Cav directly via a Discord DM (no OpenClaw cron / model in the loop).
#
# SECRETS (2026-09-10):
#   * The Nextcloud DB password is NOT stored here any more. Queries run INSIDE
#     the nextcloud-db container, which already has the password in its own
#     environment, and it is passed via MYSQL_PWD (an env var, not argv) so it
#     never appears in `ps` output on the host or in the container. SQL goes in
#     over stdin.
#   * DISCORD_BOT_TOKEN comes from /etc/spooky-bot-audit.env (root:root 0600),
#     loaded by systemd via EnvironmentFile=. The file is also sourced directly
#     so a manual `bash /usr/local/sbin/spooky-bot-audit.sh` still works.
set -euo pipefail

BOT=2
# Allowlist: Skunk Works, Digital Infrastructure, Cav DM, Dizz DM
ALLOW="em8hs3rm td853igi msxqxxvo p6rg6rhp"
LOG=/var/log/spooky-bot-audit.log
FLAG=/var/run/spooky-bot-drift.json
ENV_FILE=/etc/spooky-bot-audit.env

# Load secrets when not already provided by systemd's EnvironmentFile.
if [ -z "${DISCORD_BOT_TOKEN:-}" ] && [ -r "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

CAV_DISCORD_ID="179481162710908928"
CAV_DM_CHANNEL="1466732342704996352"

ts() { date "+%Y-%m-%d %H:%M:%S %Z"; }

# Run one SQL statement inside nextcloud-db. The password stays in that
# container's environment; the statement is fed over stdin.
db_query() {
  printf '%s\n' "$1" | docker exec -i nextcloud-db sh -c '
    PW="${MYSQL_PASSWORD:-${MARIADB_PASSWORD:-}}"
    [ -z "$PW" ] && { echo "FATAL: no DB password in nextcloud-db env" >&2; exit 1; }
    MYSQL_PWD="$PW" mariadb -u nextcloud nextcloud -N
  ' 2>/dev/null | tr -d "\r"
}

discord_alert() {
  local msg="$1"
  if [ -z "${DISCORD_BOT_TOKEN:-}" ]; then
    echo "$(ts) discord alert SKIPPED — DISCORD_BOT_TOKEN unset (check $ENV_FILE)" >> "$LOG"
    return 0
  fi
  # Post to Cav's known DM channel; fall back to creating the DM channel if it 404s.
  local code
  code=$(curl -s -o /tmp/spooky-discord-resp.json -w "%{http_code}" \
    -X POST "https://discord.com/api/v10/channels/${CAV_DM_CHANNEL}/messages" \
    -H "Authorization: Bot ${DISCORD_BOT_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$(printf '{"content": %s}' "$(printf '%s' "$msg" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')")" \
    2>/dev/null || echo "000")
  if [ "$code" != "200" ] && [ "$code" != "201" ]; then
    # Resolve/create DM channel, then retry once
    local dm
    dm=$(curl -s -X POST "https://discord.com/api/v10/users/@me/channels" \
      -H "Authorization: Bot ${DISCORD_BOT_TOKEN}" \
      -H "Content-Type: application/json" \
      --data "{\"recipient_id\": \"${CAV_DISCORD_ID}\"}" 2>/dev/null \
      | python3 -c 'import json,sys;print(json.load(sys.stdin).get("id",""))' 2>/dev/null || echo "")
    if [ -n "$dm" ]; then
      curl -s -o /dev/null -X POST "https://discord.com/api/v10/channels/${dm}/messages" \
        -H "Authorization: Bot ${DISCORD_BOT_TOKEN}" \
        -H "Content-Type: application/json" \
        --data "$(printf '{"content": %s}' "$(printf '%s' "$msg" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')")" \
        2>/dev/null || true
      echo "$(ts) discord alert sent via resolved DM channel $dm" >> "$LOG"
    else
      echo "$(ts) discord alert FAILED (http $code, could not resolve DM channel)" >> "$LOG"
    fi
  else
    echo "$(ts) discord alert sent (http $code)" >> "$LOG"
  fi
}

# Current rooms the bot is attached to
CURRENT=$(db_query "SELECT token FROM oc_talk_bots_conversation WHERE bot_id=$BOT;")

removed=""
for tok in $CURRENT; do
  keep=0
  for a in $ALLOW; do [ "$tok" = "$a" ] && keep=1; done
  if [ "$keep" -eq 0 ]; then
    # Look up a human name for the report
    NAME=$(db_query "SELECT name FROM oc_talk_rooms WHERE token='$tok';")
    docker exec -u www-data nextcloud-app php occ talk:bot:remove $BOT "$tok" >/dev/null 2>&1 || true
    removed="$removed $tok($NAME)"
    echo "$(ts) REMOVED drift: $tok $NAME" >> "$LOG"
  fi
done

if [ -n "$removed" ]; then
  NOW="$(ts)"
  echo "{\"drift\":true,\"ts\":\"$NOW\",\"removed\":\"$(echo $removed | sed "s/\"/'/g")\"}" > "$FLAG"
  discord_alert "⚠️ Spooky bot drift auto-corrected: was added to$removed, removed at $NOW. Someone likely enabled it manually via the Talk UI."
else
  # clear stale flag
  rm -f "$FLAG" 2>/dev/null || true
fi
exit 0
