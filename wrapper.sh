#!/bin/bash

set -e

# Check required env vars for bjornify
BJORNIFY_VARS=(SPOTIPY_CLIENT_ID SPOTIPY_CLIENT_SECRET SPOTIPY_REDIRECT_URI DISCORD_BOT_TOKEN CHANNEL_ID)
BJORNIFY_READY=true

for var in "${BJORNIFY_VARS[@]}"; do
	if [ -z "${!var}" ]; then
		echo "Missing required env var for bjornify: $var"
		BJORNIFY_READY=false
	fi
done

# Check required env vars for hass
HASS_VARS=(HASS_DISCORD_BOT_TOKEN HASS_CHANNEL_ID HA_URL HA_ACCESS_TOKEN)
HASS_READY=true

for var in "${HASS_VARS[@]}"; do
	if [ -z "${!var}" ]; then
		echo "Missing required env var for hass: $var"
		HASS_READY=false
	fi
done

if [ "$BJORNIFY_READY" = true ]; then
	echo "Starting bjornify.py..."
	python /app/bjornify.py &
fi

# Start processes conditionally
if [ "$HASS_READY" = true ]; then
	echo "Starting hass.py..."
	python /app/hass.py &
fi

# Exit if neither are started
if [ "$BJORNIFY_READY" = false ] && [ "$HASS_READY" = false ]; then
	echo "No services started due to missing environment variables."
	exit 1
fi

# Keep container alive as long as any child process is running
wait -n
exit $?
