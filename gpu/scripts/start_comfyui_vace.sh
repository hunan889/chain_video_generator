#!/bin/bash
# Start ComfyUI VACE instance (Wan2.2 Video-to-Video style transfer)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Script lives at <repo>/gpu/scripts/, so the repo root is two levels up.
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

COMFYUI_RAW="${COMFYUI_PATH:-./ComfyUI}"
if [[ "$COMFYUI_RAW" != /* ]]; then
    COMFYUI_DIR="$(cd "$PROJECT_DIR" && realpath -m "$COMFYUI_RAW")"
else
    COMFYUI_DIR="$COMFYUI_RAW"
fi

GPU_IDS="${VACE_GPU_IDS:-6,7}"
PORT="${COMFYUI_VACE_PORT:-8190}"

cd "$COMFYUI_DIR"
export CUDA_VISIBLE_DEVICES="$GPU_IDS"

echo "Starting ComfyUI VACE on port $PORT (GPU $GPU_IDS)..."
exec "$COMFYUI_DIR/venv/bin/python" main.py \
    --listen 0.0.0.0 \
    --port "$PORT" \
    --disable-auto-launch \
    --fast fp8_matrix_mult cublas_ops autotune \
    --async-offload 4
