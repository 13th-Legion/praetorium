#!/bin/bash
# Praetorium Portal deploy script
# Prevents .env clobbering and ensures clean deploys
set -euo pipefail

SERVER="root@167.172.233.122"
REMOTE_DIR="/opt/praetorium"
LOCAL_DIR="$(dirname "$0")"

echo "=== Praetorium Deploy ==="
echo "Server: $SERVER:$REMOTE_DIR"
echo

# 1. Rsync (excluding secrets and junk)
echo "→ Syncing files (excluding .env, __pycache__, .pyc)..."
rsync -avz --exclude-from="$LOCAL_DIR/.rsync-exclude" "$LOCAL_DIR/" "$SERVER:$REMOTE_DIR/"
echo

# 2. Verify .env still has POSTGRES_PASSWORD on remote
echo "→ Verifying .env integrity..."
ENVCHECK=$(ssh "$SERVER" "grep -c POSTGRES_PASSWORD $REMOTE_DIR/.env 2>/dev/null || echo 0")
if [ "$ENVCHECK" -lt 1 ]; then
    echo "⚠️  CRITICAL: .env is missing POSTGRES_PASSWORD!"
    echo "   Restoring from backup..."
    ssh "$SERVER" "cp /root/.praetorium-env.backup $REMOTE_DIR/.env"
    echo "   Restored."
fi
echo "   .env OK ($ENVCHECK password vars found)"
echo

# 3. Rebuild and restart app container
echo "→ Rebuilding app container..."
ssh "$SERVER" "cd $REMOTE_DIR && docker compose up -d --build app 2>&1 | tail -5"
echo

# 4. Wait and health check
echo "→ Waiting for app to start..."
sleep 5
HTTP_CODE=$(ssh "$SERVER" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8100/health")
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Deploy complete — health check passed (HTTP $HTTP_CODE)"
else
    echo "❌ Health check FAILED (HTTP $HTTP_CODE)"
    echo "   Check: ssh $SERVER 'docker logs praetorium-app --tail 20'"
    exit 1
fi
