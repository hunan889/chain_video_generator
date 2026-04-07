#!/bin/bash
# Start the local vLLM VLM server (Qwen2.5-VL-7B) used by gpu/inference_worker
# for image description (e.g. first-frame analysis in story mode).
#
# Binds to 127.0.0.1 only.
#
# Usage: bash gpu/scripts/start_vllm_vlm.sh
#
# Logs:  /tmp/vllm_vlm.log
# PID:   /tmp/vllm_vlm.pid
# Stop:  bash gpu/scripts/stop_vllm_vlm.sh

set -e

MODEL_PATH="${VLM_MODEL_PATH:-/home/gime/soft/Qwen2.5-VL-7B-Instruct-Unredacted-MAX}"
PORT="${VLM_PORT:-20010}"
GPU_IDS="${VLM_GPU_IDS:-5}"
MAX_MODEL_LEN="${VLM_MAX_LEN:-8192}"
GPU_MEM_UTIL="${VLM_GPU_MEM_UTIL:-0.85}"

# Use the absolute path to the conda env's python directly.
VLLM_PYTHON="${VLLM_PYTHON:-/home/gime/soft/conda_env/llm/bin/python}"

LOG_FILE="/tmp/vllm_vlm.log"
PID_FILE="/tmp/vllm_vlm.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "vLLM VLM already running (PID $(cat "$PID_FILE")) — refusing to start a duplicate"
    exit 1
fi

CUDA_VISIBLE_DEVICES="$GPU_IDS" nohup "$VLLM_PYTHON" -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_PATH" \
    --served-model-name Qwen2.5-VL-7B-Instruct-Unredacted-MAX \
    --tensor-parallel-size 1 \
    --max-model-len "$MAX_MODEL_LEN" \
    --trust-remote-code \
    --gpu-memory-utilization "$GPU_MEM_UTIL" \
    --host 127.0.0.1 \
    --port "$PORT" \
    > "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
echo "vLLM VLM starting on 127.0.0.1:$PORT (model=$MODEL_PATH, GPU=$GPU_IDS)"
echo "  PID:  $(cat "$PID_FILE")"
echo "  Log:  tail -f $LOG_FILE"
echo "  Test: curl http://127.0.0.1:$PORT/v1/models"
