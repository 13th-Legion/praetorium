#!/bin/bash
# Praetorium Portal deploy script
# Prevents .env clobbering and ensures clean deploys
# pipefail is CRITICAL: several steps pipe remote output to `tail`, and without
# it a failed `docker build` / `alembic upgrade` exits 0 (tail's status) and the
# deploy would falsely report success. See the 2026-07-27 squash deploy where a
# two-heads migration failure was masked exactly this way.
set -euo pipefail

SERVER="root@167.172.233.122"
REMOTE_DIR="/opt/praetorium"
LOCAL_DIR="$(dirname "$0")"

echo "=== Praetorium Deploy ==="
echo "Server: $SERVER:$REMOTE_DIR"
echo

# 1. Rsync (excluding secrets and junk) WITH --delete so files removed locally
#    (e.g. squashed-away migrations) don't linger on the server and create
#    duplicate alembic heads. --delete respects the exclude list as "protect",
#    and we add explicit protective --filter rules for anything runtime/secret
#    that must never be deleted even though it isn't in the local tree.
#
#    Persistent app data (DB, library, newsletters) lives in NAMED DOCKER
#    VOLUMES, not host dirs under $REMOTE_DIR, so --delete here cannot touch it.
#
#    SAFETY GATE: a dry-run first refuses to delete anything that is tracked in
#    git HEAD. If the dry-run wants to remove a tracked file, the local tree is
#    behind the server for that path — abort rather than nuke live source.
RSYNC_PROTECT=(
    --filter="protect .env"
    --filter="protect .env.*"
    --filter="protect .git"
    --filter="protect /uploads"
    --filter="protect /data"
    --filter="protect /media"
)

echo "→ Pre-sync safety gate (dry-run: would --delete touch any git-tracked file?)..."
DRY_DELETES=$(rsync -az --delete --dry-run --out-format='%o %n' \
    --exclude-from="$LOCAL_DIR/.rsync-exclude" "${RSYNC_PROTECT[@]}" \
    "$LOCAL_DIR/" "$SERVER:$REMOTE_DIR/" 2>/dev/null \
    | awk '$1=="del."{ $1=""; sub(/^ /,""); print }')
TRACKED_HIT=0
while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$f" in */) continue ;; esac   # skip dir entries
    if git -C "$LOCAL_DIR" cat-file -e "HEAD:$f" 2>/dev/null; then
        echo "   ⚠️  refuses to delete git-tracked file: $f"
        TRACKED_HIT=1
    fi
done <<< "$DRY_DELETES"
if [ "$TRACKED_HIT" -eq 1 ]; then
    echo "❌ Aborting: --delete would remove git-tracked source (local tree is behind"
    echo "   the server). Reconcile the workspace with the server before deploying."
    exit 1
fi
DEL_COUNT=$(printf '%s\n' "$DRY_DELETES" | grep -c . || true)
echo "   OK — ${DEL_COUNT:-0} untracked/stale path(s) will be pruned, no tracked source at risk."
echo

# 1b. LEAK GATE: .rsync-exclude is an allowlist (deny-by-default) because this
#     workspace holds ~860 untracked personal files beside the app. This asserts
#     the filter still resolves that way before anything moves. On 2026-09-16
#     MEMORY.md (psql/Cloudflare/DigitalOcean/WordPress/Discord credentials),
#     USER.md, SOUL.md and 172 memory/ files were found at rest in $REMOTE_DIR
#     and baked into the image layers. Never again silently.
#     THE INVARIANT: every file shipped must be tracked in git. Untracked means
#     "not application source" in this workspace, and that single rule is what
#     makes the deploy safe — a top-level allowlist alone is not enough, because
#     an allowlisted DIRECTORY still carries untracked files inside it. That was
#     real: scripts/nc-droplet-downsize.sh (untracked, hardcoded DigitalOcean
#     token) shipped inside `+ /scripts/***`.
echo "→ Leak gate (is every path in the transfer set git-tracked?)..."
XFER=$(rsync -an --out-format='%n' \
    --exclude-from="$LOCAL_DIR/.rsync-exclude" "${RSYNC_PROTECT[@]}" \
    "$LOCAL_DIR/" "$SERVER:$REMOTE_DIR/" 2>/dev/null \
    | grep -v '/$' | grep -vx '\.' || true)
UNTRACKED_XFER=$(comm -23 \
    <(printf '%s\n' "$XFER" | grep -v '^$' | sort) \
    <(git -C "$LOCAL_DIR" ls-files | sort))
if [ -n "$UNTRACKED_XFER" ]; then
    echo "❌ Aborting: the transfer set contains untracked files."
    echo "   Untracked means it is not application source — it must not deploy."
    printf '%s\n' "$UNTRACKED_XFER" | head -30 | sed 's/^/   /'
    echo "   Either 'git add' it (if it really is app source) or add it to"
    echo "   .rsync-exclude. Do not bypass this gate."
    exit 1
fi
echo "   OK — all $(printf '%s\n' "$XFER" | grep -c .) transferred files are git-tracked."

# Backstop: catches the inverse mistake — a private file that got git-added, so
# the tracked-only rule above would happily wave it through.
PRIVATE_RE='^(MEMORY|USER|SOUL|IDENTITY|AGENTS|HEARTBEAT|DREAMS|UNIT_CONTEXT|TOOLS)\.md$|^memory/|^\.openclaw/|^(tmp|archive|projects|area-studies|skills|references|recon-images|html-reports)/|^\.env|^tmp_'
LEAKS=$(printf '%s\n' "$XFER" | grep -E "$PRIVATE_RE" || true)
if [ -n "$LEAKS" ]; then
    echo "❌ Aborting: transfer set contains known-private paths (tracked or not):"
    printf '%s\n' "$LEAKS" | head -20 | sed 's/^/   /'
    exit 1
fi
echo "   OK — no known-private paths in the transfer set."
echo

echo "→ Syncing files (--delete; protecting .env/.git/runtime dirs)..."
rsync -avz --delete --info=DEL \
    --exclude-from="$LOCAL_DIR/.rsync-exclude" "${RSYNC_PROTECT[@]}" \
    "$LOCAL_DIR/" "$SERVER:$REMOTE_DIR/"
echo

# 1c. COMPLETENESS GATE: the flip side of a deny-by-default filter. A too-broad
#     exclude pattern would not error — it would quietly stop shipping app code
#     and the deploy would still report success. Assert the runtime-critical
#     paths actually landed. Add to this list when you add a top-level file the
#     app or a production cron job needs.
echo "→ Verifying required application paths landed on the server..."
REQUIRED_REMOTE_PATHS=(
    app/main.py
    config.py
    alembic.ini
    requirements.txt
    Dockerfile
    docker-compose.yml
    migrations/env.py
    tests/conftest.py
    scripts/ot-instructor-notify.py   # root crontab, hourly Chicago-time guard
    leave_return_runner.py            # root crontab, 06:15 daily via docker exec
)
MISSING_PATHS=$(ssh "$SERVER" "cd $REMOTE_DIR && for p in ${REQUIRED_REMOTE_PATHS[*]}; do [ -e \"\$p\" ] || echo \"\$p\"; done")
if [ -n "$MISSING_PATHS" ]; then
    echo "❌ Aborting: required application paths are missing after sync:"
    printf '%s\n' "$MISSING_PATHS" | sed 's/^/   /'
    echo "   An .rsync-exclude rule is too broad — it dropped shipped code."
    exit 1
fi
echo "   OK — all ${#REQUIRED_REMOTE_PATHS[@]} required paths present."
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

# 3. Rebuild app container. Capture the build result explicitly — piping to
#    `tail` would otherwise hide a non-zero docker exit (pipefail covers this,
#    but keep the check obvious).
echo "→ Rebuilding app container..."
if ! ssh "$SERVER" "cd $REMOTE_DIR && set -o pipefail && docker compose up -d --build app 2>&1 | tail -20"; then
    echo "❌ Container build/up FAILED — aborting deploy (no migration run)."
    echo "   Check: ssh $SERVER 'cd $REMOTE_DIR && docker compose logs app --tail 40'"
    exit 1
fi
echo

# 4. Back up the DB, then apply migrations (deploy used to skip this, causing
#    code/schema drift). Fail the deploy if the migration fails.
echo "→ Backing up database before migration..."
# pg_dump needs PGPASSWORD (sourced from the container's own env) — without it
# the dump silently produces an empty file. Run the whole thing remotely via a
# heredoc (no fragile nested SSH quoting), verify the gzip is >1KB, and ABORT
# the deploy if the backup looks empty so we never migrate without a restore
# point.
if ssh "$SERVER" 'bash -s' <<'REMOTE_BACKUP'
set -e
BK="/root/praetorium-predeploy-$(date +%Y%m%d-%H%M%S).sql.gz"
PW=$(docker exec praetorium-db printenv POSTGRES_PASSWORD)
docker exec -e PGPASSWORD="$PW" praetorium-db pg_dump -U praetorium praetorium | gzip > "$BK"
SZ=$(stat -c%s "$BK" 2>/dev/null || echo 0)
echo "   backup: $BK ($SZ bytes)"
ls -t /root/praetorium-predeploy-*.sql.gz | tail -n +6 | xargs -r rm
[ "$SZ" -gt 1024 ]
REMOTE_BACKUP
then
    echo "   DB backup OK (keeps last 5)"
else
    echo "❌ DB backup empty/failed — aborting deploy before migration."; exit 1
fi
echo

# Guard: refuse to migrate if alembic has more than one head (the exact failure
# mode from the 2026-07-27 deploy, when stale migration files lingered on the
# server and produced two heads). --delete in step 1 should prevent this now,
# but verify explicitly before touching the DB.
echo "→ Checking for a single Alembic head..."
HEAD_COUNT=$(ssh "$SERVER" "cd $REMOTE_DIR && docker exec praetorium-app alembic heads 2>/dev/null | grep -c '(head)'")
if [ "$HEAD_COUNT" != "1" ]; then
    echo "❌ Alembic has $HEAD_COUNT heads (expected 1) — aborting before migration."
    echo "   Stale migration files on the server? Check: ssh $SERVER 'ls $REMOTE_DIR/migrations/versions/'"
    exit 1
fi
echo "   Single head OK."

# Apply migrations. pipefail (set at top) makes the `| tail` honour alembic's
# real exit code, so a failed upgrade aborts the deploy instead of printing a
# false "Migrations applied".
echo "→ Applying Alembic migrations (alembic upgrade head)..."
if ssh "$SERVER" "cd $REMOTE_DIR && set -o pipefail && docker exec praetorium-app alembic upgrade head 2>&1 | tail -12"; then
    echo "   Migrations applied."
else
    echo "❌ Migration FAILED — aborting deploy. DB backup is in /root/praetorium-predeploy-*.sql.gz"
    echo "   Restore: ssh $SERVER 'gunzip -c <backup>.sql.gz | docker exec -i praetorium-db psql -U praetorium praetorium'"
    echo "   Check:   ssh $SERVER 'docker logs praetorium-app --tail 30'"
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
