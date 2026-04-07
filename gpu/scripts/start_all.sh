#!/bin/bash
# Start all GPU services on 148 in dependency order:
#   1. vLLM LLM    (Qwen3-14B, GPU 1+2 TP=2, port 20001)
#   2. vLLM VLM    (Qwen2.5-VL-7B, GPU 5, port 20010)
#   3. inference_worker (GPU 7, BLPOPs queue:inference)
#
# ComfyUI A14B instances are managed separately (start_comfyui_a14b.sh).
#
# Run from the repo root:
#   bash gpu/scripts/start_all.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "$SCRIPT_DIR/start_vllm_llm.sh"
sleep 2
bash "$SCRIPT_DIR/start_vllm_vlm.sh"
sleep 2

# Wait for vLLM endpoints to come up before starting the worker
# (worker fails fast if it can't reach them at first request, but we
# prefer to know now if something's broken).
echo "Waiting for vLLM LLM (127.0.0.1:20001) ..."
for i in $(seq 1 60); do
    if curl -sf http://127.0.0.1:20001/v1/models >/dev/null 2>&1; then
        echo "  LLM ready"
        break
    fi
    sleep 2
done

echo "Waiting for vLLM VLM (127.0.0.1:20010) ..."
for i in $(seq 1 60); do
    if curl -sf http://127.0.0.1:20010/v1/models >/dev/null 2>&1; then
        echo "  VLM ready"
        break
    fi
    sleep 2
done

bash "$SCRIPT_DIR/start_inference_worker.sh"

echo
echo "All GPU services started. Logs:"
echo "  tail -f /tmp/vllm_llm.log /tmp/vllm_vlm.log /tmp/inference_worker.log"
