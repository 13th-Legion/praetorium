#!/bin/bash
# Deploy 47th Roster to live server

HOST="ftp.47th.info"
USER="thinfo"
REMOTE_PATH="/home/thinfo/public_html/dfw"
LOCAL_PATH="/home/lkavadas/clawd/projects/47th-roster"

cd "$LOCAL_PATH"

echo "=== Deploying 47th Roster to $HOST ==="

# Create batch file for sftp
cat > /tmp/sftp_batch.txt << BATCH
-mkdir ${REMOTE_PATH}
-mkdir ${REMOTE_PATH}/assets
-mkdir ${REMOTE_PATH}/assets/css
-mkdir ${REMOTE_PATH}/assets/ranks
-mkdir ${REMOTE_PATH}/assets/ribbons
-mkdir ${REMOTE_PATH}/assets/timeInService
-mkdir ${REMOTE_PATH}/data
put index.html ${REMOTE_PATH}/index.html
put roster.html ${REMOTE_PATH}/roster.html
put awards.html ${REMOTE_PATH}/awards.html
put admin.html ${REMOTE_PATH}/admin.html
put assets/logo.png ${REMOTE_PATH}/assets/logo.png
put assets/css/47th-theme.css ${REMOTE_PATH}/assets/css/47th-theme.css
put data/roster.json ${REMOTE_PATH}/data/roster.json
put data/awards.json ${REMOTE_PATH}/data/awards.json
BATCH

# Add all rank images
for f in assets/ranks/*.png; do
  echo "put $f ${REMOTE_PATH}/$f" >> /tmp/sftp_batch.txt
done

# Add all ribbon images
for f in assets/ribbons/*.png; do
  echo "put $f ${REMOTE_PATH}/$f" >> /tmp/sftp_batch.txt
done

# Add all time in service images
for f in assets/timeInService/*.png; do
  echo "put $f ${REMOTE_PATH}/$f" >> /tmp/sftp_batch.txt
done

echo "quit" >> /tmp/sftp_batch.txt

# Run sftp with batch file
sftp -oStrictHostKeyChecking=no -b /tmp/sftp_batch.txt ${USER}@${HOST}

echo "=== Deploy complete ==="
