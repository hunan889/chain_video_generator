#!/bin/bash
# Start the local vLLM LLM server (Qwen3-14B) used by gpu/inference_worker.
#
# Binds to 127.0.0.1 only — the inference_worker is on the same box (148)
# so loopback is sufficient and avoids any public exposure.
#
# Usage: bash gpu/scripts/start_vllm_llm.sh
#
# Logs:  /tmp/vllm_llm.log
# PID:   /tmp/vllm_llm.pid
# Stop:  bash gpu/scripts/stop_vllm_llm.sh

set -e

MODEL_PATH="${LLM_MODEL_PATH:-/home/gime/soft/Qwen3-14B-v2-Abliterated}"
PORT="${LLM_PORT:-20001}"
GPU_IDS="${LLM_GPU_IDS:-1,2}"
TENSOR_PARALLEL_SIZE="${LLM_TP_SIZE:-2}"
MAX_MODEL_LEN="${LLM_MAX_LEN:-32768}"
GPU_MEM_UTIL="${LLM_GPU_MEM_UTIL:-0.85}"

# Use the absolute path to the conda env's python directly to avoid having
# to know whether the env is at miniconda3/envs/llm or conda_env/llm.
VLLM_PYTHON="${VLLM_PYTHON:-/home/gime/soft/conda_env/llm/bin/python}"

LOG_FILE="/tmp/vllm_llm.log"
PID_FILE="/tmp/vllm_llm.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "vLLM LLM already running (PID $(cat "$PID_FILE")) — refusing to start a duplicate"
    exit 1
fi

CUDA_VISIBLE_DEVICES="$GPU_IDS" nohup "$VLLM_PYTHON" -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_PATH" \
    --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
    --max-model-len "$MAX_MODEL_LEN" \
    --trust-remote-code \
    --gpu-memory-utilization "$GPU_MEM_UTIL" \
    --host 127.0.0.1 \
    --port "$PORT" \
    > "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
echo "vLLM LLM starting on 127.0.0.1:$PORT (model=$MODEL_PATH, GPUs=$GPU_IDS, TP=$TENSOR_PARALLEL_SIZE)"
echo "  PID:  $(cat "$PID_FILE")"
echo "  Log:  tail -f $LOG_FILE"
echo "  Test: curl http://127.0.0.1:$PORT/v1/models"
