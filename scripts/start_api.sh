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

cd "$PROJECT_DIR"
echo "Starting Wan2.2 Video Generation API on port $PORT..."
exec python -m uvicorn api.main:app --host "$HOST" --port "$PORT"
