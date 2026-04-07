#!/bin/bash
# Stop the vLLM LLM server started by start_vllm_llm.sh
set -e
PID_FILE="/tmp/vllm_llm.pid"
if [ ! -f "$PID_FILE" ]; then
    echo "No PID file at $PID_FILE — pkill -f vllm.entrypoints.openai.api_server.*Qwen3 instead?"
    exit 1
fi
PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "Sent SIGTERM to vLLM LLM (PID $PID)"
else
    echo "vLLM LLM PID $PID not running (stale pid file)"
fi
rm -f "$PID_FILE"
