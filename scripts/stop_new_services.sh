#!/usr/bin/env bash
# Stop the new API Gateway + GPU Worker services.
# Does NOT touch the existing service on port 8000.
#
# Usage:
#   bash scripts/stop_new_services.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

stop_pid_file() {
    local pidfile="$1"
    local name="$2"
    if [ -f "$pidfile" ]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            info "Stopped $name (PID $pid)"
        else
            warn "$name PID $pid not running (stale pidfile)"
        fi
        rm -f "$pidfile"
    else
        warn "No pidfile found for $name ($pidfile)"
    fi
}

stop_pid_file api_gateway.pid "API Gateway"
stop_pid_file gpu_worker.pid  "GPU Worker"

info "Done."
