#!/bin/bash
# Stop the gpu/inference_worker started by start_inference_worker.sh
set -e
PID_FILE="/tmp/inference_worker.pid"
if [ ! -f "$PID_FILE" ]; then
    echo "No PID file at $PID_FILE — pkill -f gpu.inference_worker.main instead?"
    exit 1
fi
PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "Sent SIGTERM to inference_worker (PID $PID)"
else
    echo "inference_worker PID $PID not running (stale pid file)"
fi
rm -f "$PID_FILE"
