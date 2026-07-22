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

# 3. Rebuild app container
echo "→ Rebuilding app container..."
ssh "$SERVER" "cd $REMOTE_DIR && docker compose up -d --build app 2>&1 | tail -5"
echo

# 4. Back up the DB, then apply migrations (deploy used to skip this, causing
#    code/schema drift). Fail the deploy if the migration fails.
echo "→ Backing up database before migration..."
ssh "$SERVER" "cd $REMOTE_DIR && docker compose exec -T db pg_dump -U praetorium praetorium 2>/dev/null | gzip > /root/praetorium-predeploy-\$(date +%Y%m%d-%H%M%S).sql.gz && ls -t /root/praetorium-predeploy-*.sql.gz | tail -n +6 | xargs -r rm" \
    && echo "   DB backup written to /root/praetorium-predeploy-*.sql.gz (keeps last 5)" \
    || echo "   ⚠️  DB backup step reported an issue — review before proceeding"
echo

echo "→ Applying Alembic migrations (alembic upgrade head)..."
if ssh "$SERVER" "cd $REMOTE_DIR && docker exec praetorium-app alembic upgrade head 2>&1 | tail -8"; then
    echo "   Migrations applied."
else
    echo "❌ Migration FAILED — aborting deploy. DB backup is in /root/praetorium-predeploy-*.sql.gz"
    echo "   Check: ssh $SERVER 'docker logs praetorium-app --tail 30'"
    exit 1
fi
echo

# 5. Wait and readiness check (hits the DB, not a static string)
echo "→ Waiting for app to start..."
sleep 5
HTTP_CODE=$(ssh "$SERVER" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8100/health/ready")
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Deploy complete — readiness check passed (HTTP $HTTP_CODE)"
else
    echo "❌ Readiness check FAILED (HTTP $HTTP_CODE)"
    echo "   Check: ssh $SERVER 'docker logs praetorium-app --tail 20'"
    exit 1
fi
