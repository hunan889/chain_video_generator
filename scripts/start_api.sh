#!/bin/bash
# Start the FastAPI service
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

HOST="${API_HOST:-0.0.0.0}"
PORT="${API_PORT:-8000}"
WORKERS="${API_WORKERS:-4}"

cd "$PROJECT_DIR"
echo "Starting Wan2.2 Video Generation API on port $PORT with $WORKERS workers..."
exec /home/gime/soft/miniconda3/bin/python -m uvicorn api.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS" 2>&1 | tee -a /tmp/wan22_api_full.log
