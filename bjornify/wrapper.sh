#!/bin/bash

set -e

LOG_LEVEL="${LOG_LEVEL:-INFO}"
LOG_LEVEL="${LOG_LEVEL^^}" # normalize to uppercase
LOG_FORMAT="${LOG_FORMAT:-plain}"
LOG_FORMAT="${LOG_FORMAT,,}" # normalize to lowercase

# Map log levels to numeric values
log_level_value() {
	case "$1" in
	DEBUG) echo 10 ;;
	INFO) echo 20 ;;
	WARNING) echo 30 ;;
	ERROR) echo 40 ;;
	CRITICAL) echo 50 ;;
	*) echo 20 ;; # default = INFO
	esac
}

# Escape JSON string
json_escape() {
	printf '%s' "$1" | sed \
		-e 's/\\/\\\\/g' \
		-e 's/"/\\"/g' \
		-e 's/\t/\\t/g' \
		-e 's/\r/\\r/g' \
		-e 's/\n/\\n/g'
}

log() {
	local level="$1"
	shift

	local msg="$*"

	local level_val
	local threshold_val

	level_val=$(log_level_value "$level")
	threshold_val=$(log_level_value "$LOG_LEVEL")

	# Only print if level >= LOG_LEVEL
	if [ "$level_val" -lt "$threshold_val" ]; then
		return 0
	fi

	if [ "$LOG_FORMAT" = "json" ]; then
		local ts
		ts=$(date +"%Y-%m-%dT%H:%M:%S.%6N")

		local esc_msg
		esc_msg=$(json_escape "$msg")

		printf '{"timestamp": "%s", "level": "%s", "logger": "wrapper.sh", "message": "%s"}\n' \
			"$ts" "$level" "$esc_msg"
		return 0
	fi

	printf "[%s] %s\n" "$level" "$msg"
}

PID_FILE="/tmp/bjornify.pid"

# Healthcheck: verify that at least one child PID is still alive
if [ "$1" = "healthcheck" ]; then
	if [ ! -f "$PID_FILE" ]; then
		log CRITICAL "PID file not found"
		exit 1
	fi

	hc_pids=()
	# Read PIDs from file
	while IFS= read -r line; do
		[ -n "$line" ] && hc_pids+=("$line")
	done <"$PID_FILE"

	if [ "${#hc_pids[@]}" -eq 0 ]; then
		log CRITICAL "PID file is empty"
		exit 1
	fi

	for pid in "${hc_pids[@]}"; do
		if kill -0 "$pid" 2>/dev/null; then
			log DEBUG "Healthcheck successful"
			exit 0
		fi
	done

	log CRITICAL "No tracked processes are running"
	exit 1
fi

# Forward signals to child processes
trap 'log INFO "Received SIGINT"; kill -SIGINT "${pids[@]}"' SIGINT
trap 'log INFO "Received SIGTERM"; kill -SIGTERM "${pids[@]}"' SIGTERM

# Check required env vars for bjornify
BJORNIFY_VARS=(SPOTIPY_CLIENT_ID SPOTIPY_CLIENT_SECRET DISCORD_BOT_TOKEN CHANNEL_ID)
BJORNIFY_READY=true

for var in "${BJORNIFY_VARS[@]}"; do
	if [ -z "${!var}" ]; then
		log INFO "Missing required env var for bjornify: $var"
		BJORNIFY_READY=false
	fi
done

# Check required env vars for hass
HASS_VARS=(HASS_DISCORD_BOT_TOKEN HASS_CHANNEL_ID HA_URL HA_ACCESS_TOKEN)
HASS_READY=true

for var in "${HASS_VARS[@]}"; do
	if [ -z "${!var}" ]; then
		log INFO "Missing required env var for hass: $var"
		HASS_READY=false
	fi
done

# Start processes conditionally and collect their PIDs
pids=()

if [ "$BJORNIFY_READY" = true ]; then
	log INFO "Starting bjornify.py..."
	python /app/bjornify.py &
	pids+=($!)
fi

if [ "$HASS_READY" = true ]; then
	log INFO "Starting hass.py..."
	python /app/hass.py &
	pids+=($!)
fi

# Exit if neither was started
if [ "$BJORNIFY_READY" = false ] && [ "$HASS_READY" = false ]; then
	log CRITICAL "No services started due to missing environment variables"
	exit 1
fi

# Persist PIDs for healthcheck
if [ "${#pids[@]}" -gt 0 ]; then
	printf "%s\n" "${pids[@]}" >"$PID_FILE"
fi

# Wait for all child processes and capture the last non-zero exit code
exit_code=0
for pid in "${pids[@]}"; do
	wait "$pid" || exit_code=$?
done

rm -f "$PID_FILE"

exit $exit_code
