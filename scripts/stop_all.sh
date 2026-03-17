#!/bin/bash
# Stop all Wan2.2 services
echo "=== Stopping Wan2.2 Video Services ==="

screen -S comfyui_a14b -X quit 2>/dev/null && echo "Stopped ComfyUI A14B" || echo "ComfyUI A14B not running"
screen -S wan22_api -X quit 2>/dev/null && echo "Stopped API server" || echo "API server not running"

echo "=== Done ==="
