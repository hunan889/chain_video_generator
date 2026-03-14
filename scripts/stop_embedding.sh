#!/bin/bash
# Stop Qwen2.5-Embedding-7B service

# Kill screen session
screen -S qwen_embedding -X quit 2>/dev/null

# Kill process by port
PID=$(lsof -ti:20002 2>/dev/null)
if [ -n "$PID" ]; then
    kill -9 $PID
    echo "Killed process on port 20002 (PID: $PID)"
fi

echo "Qwen2.5-Embedding-7B service stopped"
