#!/bin/bash

set -e

# Forward signals to child processes
trap 'echo "[INFO] Received SIGINT"; kill -SIGINT "${pids[@]}"' SIGINT
trap 'echo "[INFO] Received SIGTERM"; kill -SIGTERM "${pids[@]}"' SIGTERM

# Check required env vars for bjornify
BJORNIFY_VARS=(SPOTIPY_CLIENT_ID SPOTIPY_CLIENT_SECRET DISCORD_BOT_TOKEN CHANNEL_ID)
BJORNIFY_READY=true

for var in "${BJORNIFY_VARS[@]}"; do
	if [ -z "${!var}" ]; then
		echo "[INFO] Missing required env var for bjornify: $var"
		BJORNIFY_READY=false
	fi
done

# Check required env vars for hass
HASS_VARS=(HASS_DISCORD_BOT_TOKEN HASS_CHANNEL_ID HA_URL HA_ACCESS_TOKEN)
HASS_READY=true

for var in "${HASS_VARS[@]}"; do
	if [ -z "${!var}" ]; then
		echo "[INFO] Missing required env var for hass: $var"
		HASS_READY=false
	fi
done

# Start processes conditionally and collect their PIDs
pids=()

if [ "$BJORNIFY_READY" = true ]; then
	echo "[INFO] Starting bjornify.py..."
	python /app/bjornify.py &
	pids+=($!)
fi

if [ "$HASS_READY" = true ]; then
	echo "[INFO] Starting hass.py..."
	python /app/hass.py &
	pids+=($!)
fi

# Exit if neither was started
if [ "$BJORNIFY_READY" = false ] && [ "$HASS_READY" = false ]; then
	echo "[CRITICAL] No services started due to missing environment variables."
	exit 1
fi

# Wait for all child processes and capture the last non-zero exit code
exit_code=0
for pid in "${pids[@]}"; do
	wait "$pid" || exit_code=$?
done

exit $exit_code
