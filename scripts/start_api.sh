#!/bin/bash
# Start API service with gunicorn (supports graceful reload)
# Usage: bash scripts/start_api.sh
#
# After starting, use `bash scripts/reload_api.sh` for zero-downtime restarts.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_DIR/gunicorn.pid"
LOG_FILE="$PROJECT_DIR/api.log"
PYTHON="${PYTHON:-/home/gime/soft/miniconda3/bin/python}"

if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

HOST="${API_HOST:-0.0.0.0}"
PORT="${API_PORT:-8000}"

# Check if already running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "gunicorn is already running (PID: $PID). Use reload_api.sh for graceful restart."
        exit 0
    fi
    echo "Removing stale PID file..."
    rm -f "$PID_FILE"
fi

echo "Starting gunicorn with uvicorn worker on $HOST:$PORT..."
cd "$PROJECT_DIR"
nohup $PYTHON -m gunicorn api.main:app \
    -k uvicorn.workers.UvicornWorker \
    -w 1 \
    --bind "$HOST:$PORT" \
    --pid "$PID_FILE" \
    --graceful-timeout 180 \
    --timeout 600 \
    --access-logfile - \
    >> "$LOG_FILE" 2>&1 &

sleep 2

if [ -f "$PID_FILE" ]; then
    echo "gunicorn started (PID: $(cat "$PID_FILE"))"
else
    echo "Warning: PID file not created yet, checking process..."
    sleep 2
    if [ -f "$PID_FILE" ]; then
        echo "gunicorn started (PID: $(cat "$PID_FILE"))"
    else
        echo "Error: gunicorn may have failed to start. Check $LOG_FILE"
        exit 1
    fi
fi
