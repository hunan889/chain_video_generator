#!/bin/bash
# Start Qwen2.5-Embedding-7B service

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load environment
source "$PROJECT_DIR/.env" 2>/dev/null || true

# Configuration
MODEL_PATH="${EMBEDDING_MODEL_PATH:-/home/gime/soft/Qwen2.5-Embedding-7B}"
PORT="${EMBEDDING_PORT:-20002}"
GPU_ID="${EMBEDDING_GPU_ID:-3}"
MAX_MODEL_LEN="${EMBEDDING_MAX_LEN:-8192}"

# Activate conda environment
source /home/gime/soft/miniconda3/bin/activate llm

# Start vLLM server in screen
screen -dmS qwen_embedding bash -c "
  source /home/gime/soft/miniconda3/bin/activate llm && \
  CUDA_VISIBLE_DEVICES=$GPU_ID python -m vllm.entrypoints.openai.api_server \
    --model $MODEL_PATH \
    --port $PORT \
    --host 0.0.0.0 \
    --max-model-len $MAX_MODEL_LEN \
    --dtype auto \
    --trust-remote-code \
    2>&1 | tee /tmp/qwen_embedding.log
"

echo "Qwen2.5-Embedding-7B service starting on port $PORT (GPU $GPU_ID)"
echo "Check logs: tail -f /tmp/qwen_embedding.log"
echo "Check screen: screen -r qwen_embedding"
