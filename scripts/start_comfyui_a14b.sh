#!/bin/bash
# Start ComfyUI A14B instance
# Supports multiple instances via INSTANCE_ID env var (default: 0)
# Usage:
#   INSTANCE_ID=0 bash scripts/start_comfyui_a14b.sh  # GPU 2, port 8188
#   INSTANCE_ID=1 bash scripts/start_comfyui_a14b.sh  # GPU 3, port 8191
#   INSTANCE_ID=2 bash scripts/start_comfyui_a14b.sh  # GPU 4, port 8192
#   INSTANCE_ID=3 bash scripts/start_comfyui_a14b.sh  # GPU 7, port 8193
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

INSTANCE_ID="${INSTANCE_ID:-0}"

# Per-instance GPU and port configuration (from .env or defaults)
case "$INSTANCE_ID" in
    0)
        GPU_IDS="${A14B_GPU_IDS:-2}"
        PORT="${COMFYUI_A14B_PORT:-8188}"
        ;;
    1)
        GPU_IDS="${A14B_GPU_IDS_1:-3}"
        PORT="${COMFYUI_A14B_PORT_1:-8191}"
        ;;
    2)
        GPU_IDS="${A14B_GPU_IDS_2:-4}"
        PORT="${COMFYUI_A14B_PORT_2:-8192}"
        ;;
    3)
        GPU_IDS="${A14B_GPU_IDS_3:-7}"
        PORT="${COMFYUI_A14B_PORT_3:-8193}"
        ;;
    *)
        echo "ERROR: Unknown INSTANCE_ID=$INSTANCE_ID (valid: 0-3)"
        exit 1
        ;;
esac

cd "$COMFYUI_DIR"
export CUDA_VISIBLE_DEVICES="$GPU_IDS"

echo "Starting ComfyUI A14B #$INSTANCE_ID on port $PORT (GPU $GPU_IDS)..."
exec "$COMFYUI_DIR/venv/bin/python" main.py \
    --listen 0.0.0.0 \
    --port "$PORT" \
    --disable-auto-launch \
    --fast fp8_matrix_mult cublas_ops autotune \
    --async-offload 4
