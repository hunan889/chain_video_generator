#!/bin/bash
# Start ComfyUI 5B instance
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

COMFYUI_RAW="${COMFYUI_PATH:-./ComfyUI}"
if [[ "$COMFYUI_RAW" != /* ]]; then
    COMFYUI_DIR="$(cd "$PROJECT_DIR" && realpath -m "$COMFYUI_RAW")"
else
    COMFYUI_DIR="$COMFYUI_RAW"
fi

GPU_ID="${FIVE_B_GPU_ID:-2}"
PORT="${COMFYUI_5B_PORT:-8189}"

cd "$COMFYUI_DIR"
export CUDA_VISIBLE_DEVICES="$GPU_ID"

echo "Starting ComfyUI 5B on port $PORT (GPU $GPU_ID)..."
exec "$COMFYUI_DIR/venv/bin/python" main.py \
    --listen 0.0.0.0 \
    --port "$PORT" \
    --disable-auto-launch
