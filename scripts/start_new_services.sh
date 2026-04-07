#!/usr/bin/env bash
# Start the new API Gateway + GPU Worker services WITHOUT touching the existing service.
# Safe to run while the original wan22 service is running on port 8000.
#
# Usage:
#   bash scripts/start_new_services.sh
#
# First-time setup:
#   1. Copy and fill in env files:
#        cp api_gateway/.env.example api_gateway/.env && nano api_gateway/.env
#        cp gpu/comfyui_worker/.env.example gpu/comfyui_worker/.env && nano gpu/comfyui_worker/.env
#   2. Run this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
if [ ! -f api_gateway/.env ]; then
    error "api_gateway/.env not found."
    echo "  Run: cp api_gateway/.env.example api_gateway/.env && nano api_gateway/.env"
    exit 1
fi

if [ ! -f gpu/comfyui_worker/.env ]; then
    error "gpu/comfyui_worker/.env not found."
    echo "  Run: cp gpu/comfyui_worker/.env.example gpu/comfyui_worker/.env && nano gpu/comfyui_worker/.env"
    exit 1
fi

# Detect Python
PYTHON="${PYTHON:-python}"
if command -v conda &>/dev/null && conda env list | grep -q wan22; then
    PYTHON="$(conda run -n wan22 which python 2>/dev/null || echo python)"
fi
info "Using Python: $PYTHON ($($PYTHON --version 2>&1))"

# Check required packages
for pkg in fastapi uvicorn redis; do
    if ! $PYTHON -c "import $pkg" 2>/dev/null; then
        warn "Package '$pkg' not found. Installing api_gateway requirements..."
        $PYTHON -m pip install -r api_gateway/requirements.txt -q
        break
    fi
done

# ---------------------------------------------------------------------------
# Check port 9000 is free (new API Gateway port)
# ---------------------------------------------------------------------------
if lsof -iTCP:9000 -sTCP:LISTEN -t &>/dev/null 2>&1; then
    warn "Port 9000 is already in use — API Gateway may already be running."
fi

# ---------------------------------------------------------------------------
# Start API Gateway (background, log to api_gateway.log)
# ---------------------------------------------------------------------------
info "Starting API Gateway on port 9000..."
nohup $PYTHON -m uvicorn api_gateway.main:app \
    --host 0.0.0.0 \
    --port 9000 \
    --workers 2 \
    --log-level info \
    >> api_gateway.log 2>&1 &
echo $! > api_gateway.pid
info "API Gateway PID: $(cat api_gateway.pid) — logs: api_gateway.log"

# ---------------------------------------------------------------------------
# Start ComfyUI Worker (background, log to comfyui_worker.log)
# ---------------------------------------------------------------------------
info "Starting ComfyUI Worker..."
nohup $PYTHON -m gpu.comfyui_worker.main \
    >> comfyui_worker.log 2>&1 &
echo $! > comfyui_worker.pid
info "ComfyUI Worker PID: $(cat comfyui_worker.pid) — logs: comfyui_worker.log"

# ---------------------------------------------------------------------------
# Quick health check (wait up to 10 s)
# ---------------------------------------------------------------------------
info "Waiting for API Gateway to be ready..."
for i in $(seq 1 20); do
    if curl -sf http://127.0.0.1:9000/health >/dev/null 2>&1; then
        info "API Gateway is healthy."
        break
    fi
    sleep 0.5
done

if ! curl -sf http://127.0.0.1:9000/health >/dev/null 2>&1; then
    warn "API Gateway health check failed after 10 s — check api_gateway.log"
fi

info "Done. New services running alongside existing service (port 8000)."
echo
echo "  API Gateway:  http://localhost:9000"
echo "  Health check: curl http://localhost:9000/health"
echo "  Logs:         tail -f api_gateway.log comfyui_worker.log"
echo "  Stop:         bash scripts/stop_new_services.sh"
