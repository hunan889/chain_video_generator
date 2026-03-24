#!/bin/bash
# Stop all Wan2.2 services
echo "=== Stopping Wan2.2 Video Services ==="

# Stop all A14B instances (0-3)
for i in 0 1 2 3; do
    screen -S "comfyui_a14b_$i" -X quit 2>/dev/null && echo "Stopped ComfyUI A14B #$i" || true
done

# Legacy single-instance screen name
screen -S comfyui_a14b -X quit 2>/dev/null && echo "Stopped ComfyUI A14B (legacy)" || true

screen -S wan22_api -X quit 2>/dev/null && echo "Stopped API server" || echo "API server not running"

echo "=== Done ==="
