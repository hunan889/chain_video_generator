#!/bin/bash
# Graceful reload API service (zero-downtime)
# Usage: bash scripts/reload_api.sh
#
# This sends SIGHUP to the gunicorn master process, which:
#   1. Starts new workers with updated code
#   2. Old workers finish serving current requests
#   3. Old workers are then gracefully terminated
# Result: zero downtime, no dropped requests.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_DIR/gunicorn.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "Error: PID file not found at $PID_FILE"
    echo "Is gunicorn running? Start it with: bash scripts/start_api.sh"
    exit 1
fi

PID=$(cat "$PID_FILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Error: gunicorn process $PID is not running"
    echo "Removing stale PID file and restarting..."
    rm -f "$PID_FILE"
    bash "$SCRIPT_DIR/start_api.sh"
    exit 0
fi

echo "Sending graceful reload signal (HUP) to gunicorn master (PID: $PID)..."
kill -HUP "$PID"
echo "Reload signal sent. New workers will start with updated code."
echo "Old workers will finish current requests before exiting."
