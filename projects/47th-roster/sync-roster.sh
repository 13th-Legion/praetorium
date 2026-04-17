#!/bin/bash
# 47th Legion Roster Sync
# Fetches from Discord, builds roster, syncs ribbons, deploys to WordPress

set -e
cd "$(dirname "$0")"

LOG_FILE="sync.log"
echo "=== Roster Sync: $(date) ===" >> "$LOG_FILE"

# 1. Fetch from Discord
echo "[1/4] Fetching members from Discord..."
./fetch-roster.sh >> "$LOG_FILE" 2>&1

# 2. Build roster
echo "[2/4] Building roster.json..."
node build-roster.js >> "$LOG_FILE" 2>&1

# 2b. Merge historical data (if exists)
if [ -f "merge-historical.js" ]; then
  echo "      Merging historical data..."
  node merge-historical.js >> "$LOG_FILE" 2>&1
fi

# 3. Sync ribbons from Discord roles (additive only)
echo "[3/4] Syncing ribbons from Discord roles..."
node sync-ribbons.js >> "$LOG_FILE" 2>&1

# 4. Deploy to WordPress theme
echo "[4/4] Deploying to WordPress..."
DO_HOST="${DO_HOST:-104.248.0.128}"
DO_USER="${DO_USER:-root}"
REMOTE_WP="/var/www/47th.info/htdocs/wp-content/themes/astra-child/data"

if [ -z "$DO_PASS" ]; then
  echo "ERROR: DO_PASS environment variable not set. Export it or add to .env" >&2
  exit 1
fi

sshpass -p "$DO_PASS" scp -oStrictHostKeyChecking=no data/roster.json ${DO_USER}@${DO_HOST}:${REMOTE_WP}/roster.json
sshpass -p "$DO_PASS" scp -oStrictHostKeyChecking=no data/awards.json ${DO_USER}@${DO_HOST}:${REMOTE_WP}/awards.json
sshpass -p "$DO_PASS" ssh -oStrictHostKeyChecking=no ${DO_USER}@${DO_HOST} "chown www-data:www-data ${REMOTE_WP}/*.json"

echo "      → Deployed to WordPress theme" >> "$LOG_FILE"
echo "=== Sync complete: $(date) ===" >> "$LOG_FILE"
echo ""
echo "✅ Roster synced successfully!"
