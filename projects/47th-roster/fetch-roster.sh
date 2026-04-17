#!/bin/bash
# Fetch 47th Legion roster from Discord API

TOKEN="${DISCORD_BOT_TOKEN:?ERROR: DISCORD_BOT_TOKEN not set}"
GUILD_ID="179481732217569280"
OUTPUT="data/members-raw.json"

mkdir -p data

# Fetch all members (paginated)
echo "[]" > "$OUTPUT.tmp"
AFTER=""

while true; do
  if [ -z "$AFTER" ]; then
    RESP=$(curl -s -H "Authorization: Bot $TOKEN" "https://discord.com/api/v10/guilds/$GUILD_ID/members?limit=1000")
  else
    RESP=$(curl -s -H "Authorization: Bot $TOKEN" "https://discord.com/api/v10/guilds/$GUILD_ID/members?limit=1000&after=$AFTER")
  fi
  
  COUNT=$(echo "$RESP" | jq 'length')
  echo "Fetched $COUNT members..."
  
  if [ "$COUNT" -eq 0 ]; then
    break
  fi
  
  # Merge with existing
  jq -s '.[0] + .[1]' "$OUTPUT.tmp" <(echo "$RESP") > "$OUTPUT.tmp2"
  mv "$OUTPUT.tmp2" "$OUTPUT.tmp"
  
  # Get last user ID for pagination
  AFTER=$(echo "$RESP" | jq -r '.[-1].user.id')
  
  if [ "$COUNT" -lt 1000 ]; then
    break
  fi
  
  sleep 1
done

mv "$OUTPUT.tmp" "$OUTPUT"
echo "Done! Saved to $OUTPUT"
