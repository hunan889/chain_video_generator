#!/bin/bash
# Stop the vLLM VLM server started by start_vllm_vlm.sh
set -e
PID_FILE="/tmp/vllm_vlm.pid"
if [ ! -f "$PID_FILE" ]; then
    echo "No PID file at $PID_FILE — pkill -f vllm.entrypoints.openai.api_server.*Qwen2.5-VL instead?"
    exit 1
fi
PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "Sent SIGTERM to vLLM VLM (PID $PID)"
else
    echo "vLLM VLM PID $PID not running (stale pid file)"
fi
rm -f "$PID_FILE"
