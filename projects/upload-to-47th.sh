#!/bin/bash
# Upload Texas Area Studies to 47th.info via SFTP
# Run from: ~/clawd/projects/

HOST="ftp.47th.info"
USER="thinfo"
PASS="SPsaints!!11"
REMOTE_BASE="/home/thinfo/public_html"

# Check for sshpass
if ! command -v sshpass &> /dev/null; then
    echo "Installing sshpass..."
    sudo apt-get install -y sshpass
fi

echo "=== Uploading Texas Area Studies to 47th.info ==="

# Upload portal to /texas/
echo "[1/6] Uploading portal to /texas/..."
sshpass -p "$PASS" rsync -avz --progress \
    texas-area-studies-portal/ \
    "$USER@$HOST:$REMOTE_BASE/texas/"

# Upload individual studies
echo "[2/6] Uploading Austin..."
sshpass -p "$PASS" rsync -avz --progress \
    austin-area-study/ \
    "$USER@$HOST:$REMOTE_BASE/austin/"

echo "[3/6] Uploading Corpus Christi..."
sshpass -p "$PASS" rsync -avz --progress \
    corpus-christi-area-study/ \
    "$USER@$HOST:$REMOTE_BASE/corpus-christi/"

echo "[4/6] Uploading DFW..."
sshpass -p "$PASS" rsync -avz --progress \
    dfw-area-study/ \
    "$USER@$HOST:$REMOTE_BASE/dfw/"

echo "[5/6] Uploading Houston..."
sshpass -p "$PASS" rsync -avz --progress \
    houston-area-study/ \
    "$USER@$HOST:$REMOTE_BASE/houston/"

echo "[6/6] Uploading San Antonio..."
sshpass -p "$PASS" rsync -avz --progress \
    san-antonio-area-study/ \
    "$USER@$HOST:$REMOTE_BASE/san-antonio/"

echo ""
echo "=== Upload complete! ==="
echo "Portal: https://47th.info/texas/"
echo "Studies:"
echo "  - https://47th.info/austin/"
echo "  - https://47th.info/corpus-christi/"
echo "  - https://47th.info/dfw/"
echo "  - https://47th.info/houston/"
echo "  - https://47th.info/san-antonio/"
