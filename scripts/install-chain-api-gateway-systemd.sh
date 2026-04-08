#!/usr/bin/env bash
#
# install-chain-api-gateway-systemd.sh
#
# Installs and activates the chain-video-api-gateway.service systemd unit
# on wan170, replacing the fragile nohup-based start of api_gateway.
#
# Pre-flight: confirms venv, .env, and /health are currently OK.
# Post-flight: verifies systemctl is-active AND /health 200.
#
# This script is intended to be copied to /tmp and executed via:
#   sudo bash /tmp/install-chain-api-gateway-systemd.sh
#
# The .service file must already be present at:
#   /tmp/chain-video-api-gateway.service
#
set -euo pipefail

APP_DIR="/usr/local/soft/chain_video_api"
VENV_PY="${APP_DIR}/venv/bin/python"
ENV_FILE="${APP_DIR}/.env"
LOG_FILE="${APP_DIR}/gateway.log"
UPLOADS_DIR="${APP_DIR}/uploads"
UNIT_SRC="/tmp/chain-video-api-gateway.service"
UNIT_DST="/etc/systemd/system/chain-video-api-gateway.service"
UNIT_NAME="chain-video-api-gateway.service"
HEALTH_URL="http://127.0.0.1:9000/health"
OLD_PID_FILE="${APP_DIR}/api_gateway.pid"

log() { echo "[install-chain-api-gateway] $*"; }
die() { echo "[install-chain-api-gateway][ERROR] $*" >&2; exit 1; }

# -----------------------------------------------------------------------------
# 1. Pre-flight checks
# -----------------------------------------------------------------------------
log "Pre-flight checks..."

[[ -x "$VENV_PY" ]]      || die "venv python not found/executable at $VENV_PY"
[[ -f "$ENV_FILE" ]]     || die ".env file missing at $ENV_FILE"
[[ -f "$UNIT_SRC" ]]     || die "unit source not found at $UNIT_SRC (scp first)"
[[ -d "$UPLOADS_DIR" ]]  || { log "creating $UPLOADS_DIR"; mkdir -p "$UPLOADS_DIR"; chown root:root "$UPLOADS_DIR"; }
[[ -f "$LOG_FILE" ]]     || { log "creating $LOG_FILE"; touch "$LOG_FILE"; chown root:root "$LOG_FILE"; }

log "venv OK, .env OK, uploads/ OK, gateway.log OK"

log "Checking current /health..."
if ! curl -sf --max-time 5 "$HEALTH_URL" >/dev/null; then
    die "current api_gateway /health is NOT OK — aborting (don't break a broken service)"
fi
log "current /health OK"

# -----------------------------------------------------------------------------
# 2. Install unit file
# -----------------------------------------------------------------------------
log "Installing unit file to $UNIT_DST..."
install -m 0644 -o root -g root "$UNIT_SRC" "$UNIT_DST"
systemctl daemon-reload
log "daemon-reload complete"

# -----------------------------------------------------------------------------
# 3. Stop existing nohup-based process
# -----------------------------------------------------------------------------
log "Locating existing api_gateway process on :9000..."

OLD_PID=""
if [[ -f "$OLD_PID_FILE" ]]; then
    CANDIDATE=$(cat "$OLD_PID_FILE" 2>/dev/null || true)
    if [[ -n "$CANDIDATE" ]] && kill -0 "$CANDIDATE" 2>/dev/null; then
        OLD_PID="$CANDIDATE"
        log "found PID from api_gateway.pid: $OLD_PID"
    fi
fi

if [[ -z "$OLD_PID" ]]; then
    # Fall back to ss lookup
    OLD_PID=$(ss -tlnp 2>/dev/null | awk '/127\.0\.0\.1:9000/ {print $0}' | grep -oP 'pid=\K[0-9]+' | head -n1 || true)
    if [[ -n "$OLD_PID" ]]; then
        log "found PID via ss: $OLD_PID"
    fi
fi

if [[ -n "$OLD_PID" ]]; then
    log "killing old nohup process $OLD_PID"
    kill "$OLD_PID" 2>/dev/null || true
    for i in 1 2 3 4 5; do
        if ! kill -0 "$OLD_PID" 2>/dev/null; then
            log "old process $OLD_PID exited after ${i}s"
            break
        fi
        sleep 1
    done
    # Force if still alive
    if kill -0 "$OLD_PID" 2>/dev/null; then
        log "old process still alive, sending SIGKILL"
        kill -9 "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi
else
    log "no existing api_gateway process found on :9000 (unusual — continuing)"
fi

log "waiting 2s for port :9000 to free..."
sleep 2

# Sanity: port should be free now
if ss -tln | awk '{print $4}' | grep -q '127\.0\.0\.1:9000$'; then
    log "WARNING: port :9000 still bound, trying to identify..."
    ss -tlnp | grep ':9000' || true
    die "port :9000 still bound before starting systemd unit — aborting"
fi

# -----------------------------------------------------------------------------
# 4. Enable + start
# -----------------------------------------------------------------------------
log "Enabling and starting $UNIT_NAME..."
systemctl enable "$UNIT_NAME"
systemctl start "$UNIT_NAME"

log "waiting 3s for service to initialize..."
sleep 3

# -----------------------------------------------------------------------------
# 5. Post-flight verification
# -----------------------------------------------------------------------------
log "Verifying systemctl is-active..."
if ! systemctl is-active --quiet "$UNIT_NAME"; then
    log "ERROR: $UNIT_NAME is not active"
    log "--- last 30 lines of journalctl ---"
    journalctl -u "$UNIT_NAME" -n 30 --no-pager || true
    log "--- last 30 lines of $LOG_FILE ---"
    tail -n 30 "$LOG_FILE" || true
    die "systemd unit failed to become active"
fi
log "systemctl is-active: OK"

log "Verifying /health via new systemd-managed service..."
# Allow a small retry window — uvicorn cold-start
HEALTH_OK=0
for attempt in 1 2 3 4 5; do
    if curl -sf --max-time 5 "$HEALTH_URL" >/dev/null; then
        HEALTH_OK=1
        break
    fi
    log "attempt $attempt: /health not ready yet, retrying..."
    sleep 2
done

if [[ "$HEALTH_OK" -ne 1 ]]; then
    log "ERROR: /health failed after 5 attempts"
    log "--- last 30 lines of journalctl ---"
    journalctl -u "$UNIT_NAME" -n 30 --no-pager || true
    log "--- last 30 lines of $LOG_FILE ---"
    tail -n 30 "$LOG_FILE" || true
    die "/health check failed under systemd-managed service"
fi

MAIN_PID=$(systemctl show "$UNIT_NAME" -p MainPID --value)
log "SUCCESS — $UNIT_NAME active, MainPID=$MAIN_PID, /health OK"
log "NOTE: old $OLD_PID_FILE file left in place as a relic (not used by systemd)"
