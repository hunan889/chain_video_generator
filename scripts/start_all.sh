#!/bin/bash
# One-click start all services using screen
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

echo "=== Starting Wan2.2 Video Services ==="

# Kill existing screens if any
screen -S comfyui_a14b -X quit 2>/dev/null || true
screen -S comfyui_5b -X quit 2>/dev/null || true
screen -S wan22_api -X quit 2>/dev/null || true

sleep 1

# Start ComfyUI A14B
echo "Starting ComfyUI A14B on port ${COMFYUI_A14B_PORT:-8188}..."
screen -dmS comfyui_a14b bash "$SCRIPT_DIR/start_comfyui_a14b.sh"

# Start ComfyUI 5B if model exists
MODEL_5B="$COMFYUI_DIR/models/diffusion_models/Wan2_2-TI2V-5B-Turbo_fp16.safetensors"
if [ -f "$MODEL_5B" ] && [ ! -f "${MODEL_5B}.aria2" ]; then
    echo "Starting ComfyUI 5B on port ${COMFYUI_5B_PORT:-8189}..."
    screen -dmS comfyui_5b bash "$SCRIPT_DIR/start_comfyui_5b.sh"
else
    echo "[SKIP] 5B model not ready yet, skipping ComfyUI 5B"
fi

# Wait for ComfyUI to initialize before starting API
echo "Waiting 10s for ComfyUI to initialize..."
sleep 10

# Start FastAPI
echo "Starting API server on port ${API_PORT:-8000}..."
screen -dmS wan22_api bash "$SCRIPT_DIR/start_api.sh"

sleep 2

echo ""
echo "=== All services started ==="
screen -ls | grep -E "comfyui|wan22" || true
echo ""
echo "Commands:"
echo "  screen -r comfyui_a14b   # View A14B logs"
echo "  screen -r comfyui_5b     # View 5B logs"
echo "  screen -r wan22_api      # View API logs"
echo "  Ctrl+A then D            # Detach from screen"
echo ""
echo "Stop all:  bash $SCRIPT_DIR/stop_all.sh"
