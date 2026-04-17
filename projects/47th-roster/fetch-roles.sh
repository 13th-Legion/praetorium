#!/bin/bash
TOKEN="${DISCORD_BOT_TOKEN:?ERROR: DISCORD_BOT_TOKEN not set}"
GUILD_ID="179481732217569280"

curl -s -H "Authorization: Bot $TOKEN" "https://discord.com/api/v10/guilds/$GUILD_ID/roles" | jq -r '.[] | "\(.id)\t\(.name)"' | sort -t$'\t' -k2
