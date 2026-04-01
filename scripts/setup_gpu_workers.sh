#!/bin/bash
# Setup 4 GPU Workers as systemd services with auto-restart
# Usage: bash scripts/setup_gpu_workers.sh

set -e

PORTS=(8188 8190 8191 8192)
SERVICE_FILE="/etc/systemd/system/gpu-worker@.service"

# Install template service
cp scripts/gpu-worker@.service "$SERVICE_FILE"
echo "Installed service template: $SERVICE_FILE"

# Create per-instance overrides with correct ComfyUI port
for i in 0 1 2 3; do
    PORT=${PORTS[$i]}
    OVERRIDE_DIR="/etc/systemd/system/gpu-worker@${i}.service.d"
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

# Stop old manual workers
pkill -f 'gpu_worker/main.py' 2>/dev/null || true
sleep 2

# Start all workers
for i in 0 1 2 3; do
    systemctl enable gpu-worker@${i}
    systemctl start gpu-worker@${i}
    echo "Started gpu-worker@${i}"
done

sleep 3
echo ""
echo "=== Status ==="
for i in 0 1 2 3; do
    STATUS=$(systemctl is-active gpu-worker@${i} 2>/dev/null)
    echo "gpu-worker@${i}: $STATUS"
done

echo ""
echo "Workers will auto-restart on crash (RestartSec=10s, max 5 restarts per 5min)"
echo "Logs: journalctl -u gpu-worker@0 -f"
echo "Stop all: systemctl stop gpu-worker@{0,1,2,3}"
echo "Start all: systemctl start gpu-worker@{0,1,2,3}"
