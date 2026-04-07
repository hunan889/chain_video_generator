#!/bin/bash
# Setup 4 ComfyUI GPU Workers as systemd services with auto-restart
# Usage: bash scripts/setup_comfyui_workers.sh

set -e

PORTS=(8188 8191 8192 8193)
SERVICE_FILE="/etc/systemd/system/comfyui-worker@.service"

# Install template service
cp scripts/comfyui-worker@.service "$SERVICE_FILE"
echo "Installed service template: $SERVICE_FILE"

# Create per-instance overrides with correct ComfyUI port
for i in 0 1 2 3; do
    PORT=${PORTS[$i]}
    OVERRIDE_DIR="/etc/systemd/system/comfyui-worker@${i}.service.d"
    mkdir -p "$OVERRIDE_DIR"
    cat > "$OVERRIDE_DIR/port.conf" << EOF
[Service]
Environment=COMFYUI_URLS={"a14b": "http://127.0.0.1:${PORT}"}
Environment=COMFYUI_PORT=${PORT}
EOF
    echo "Worker $i: ComfyUI port $PORT"
done

# Reload and enable
systemctl daemon-reload

# Stop old manual workers (legacy + new module path)
pkill -f 'gpu_worker.main' 2>/dev/null || true
pkill -f 'gpu.comfyui_worker.main' 2>/dev/null || true
sleep 2

# Start all workers
for i in 0 1 2 3; do
    systemctl enable comfyui-worker@${i}
    systemctl start comfyui-worker@${i}
    echo "Started comfyui-worker@${i}"
done

sleep 3
echo ""
echo "=== Status ==="
for i in 0 1 2 3; do
    STATUS=$(systemctl is-active comfyui-worker@${i} 2>/dev/null)
    echo "comfyui-worker@${i}: $STATUS"
done

echo ""
echo "Workers will auto-restart on crash (RestartSec=10s, max 5 restarts per 5min)"
echo "Logs: journalctl -u comfyui-worker@0 -f"
echo "Stop all: systemctl stop comfyui-worker@{0,1,2,3}"
echo "Start all: systemctl start comfyui-worker@{0,1,2,3}"
