#!/bin/bash

set -e

PID_FILE="/tmp/bjornify.pid"

# Healthcheck: verify that at least one child PID is still alive
if [ "$1" = "healthcheck" ]; then
	if [ ! -f "$PID_FILE" ]; then
		echo "[CRITICAL] PID file not found"
		exit 1
	fi

	hc_pids=()
	# Read PIDs from file
	while IFS= read -r line; do
		[ -n "$line" ] && hc_pids+=("$line")
	done < "$PID_FILE"

	if [ "${#hc_pids[@]}" -eq 0 ]; then
		echo "[CRITICAL] PID file is empty"
		exit 1
	fi

	for pid in "${hc_pids[@]}"; do
		if kill -0 "$pid" 2>/dev/null; then
			exit 0
		fi
	done

	echo "[CRITICAL] No tracked processes are running"
	exit 1
fi

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
	echo "[CRITICAL] No services started due to missing environment variables"
	exit 1
fi

# Persist PIDs for healthcheck
if [ "${#pids[@]}" -gt 0 ]; then
	printf "%s\n" "${pids[@]}" > "$PID_FILE"
fi

# Wait for all child processes and capture the last non-zero exit code
exit_code=0
for pid in "${pids[@]}"; do
	wait "$pid" || exit_code=$?
done

rm -f "$PID_FILE"

exit $exit_code
