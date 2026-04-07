#!/bin/bash
# Stop all gpu/inference services started by start_all.sh.
# Does NOT touch ComfyUI A14B instances.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "$SCRIPT_DIR/stop_inference_worker.sh" || true
bash "$SCRIPT_DIR/stop_vllm_vlm.sh" || true
bash "$SCRIPT_DIR/stop_vllm_llm.sh" || true

echo "Done."
