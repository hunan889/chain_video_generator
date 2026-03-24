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

# Number of A14B instances (default: 4)
A14B_INSTANCE_COUNT="${A14B_INSTANCE_COUNT:-4}"

echo "=== Starting Wan2.2 Video Services ==="

# Kill existing screens if any
for i in $(seq 0 $((A14B_INSTANCE_COUNT - 1))); do
    screen -S "comfyui_a14b_$i" -X quit 2>/dev/null || true
done
screen -S wan22_api -X quit 2>/dev/null || true

sleep 1

# Start ComfyUI A14B instances
for i in $(seq 0 $((A14B_INSTANCE_COUNT - 1))); do
    echo "Starting ComfyUI A14B #$i..."
    screen -dmS "comfyui_a14b_$i" bash -c "INSTANCE_ID=$i bash '$SCRIPT_DIR/start_comfyui_a14b.sh'"
done

# Wait for ComfyUI instances to initialize before starting API
echo "Waiting 15s for ComfyUI instances to initialize..."
sleep 15

# Start FastAPI
echo "Starting API server on port ${API_PORT:-8000}..."
screen -dmS wan22_api bash "$SCRIPT_DIR/start_api.sh"

sleep 2

echo ""
echo "=== All services started ==="
screen -ls | grep -E "comfyui|wan22" || true
echo ""
echo "Commands:"
for i in $(seq 0 $((A14B_INSTANCE_COUNT - 1))); do
    echo "  screen -r comfyui_a14b_$i   # View A14B #$i logs"
done
echo "  screen -r wan22_api      # View API logs"
echo "  Ctrl+A then D            # Detach from screen"
echo ""
echo "Stop all:  bash $SCRIPT_DIR/stop_all.sh"
