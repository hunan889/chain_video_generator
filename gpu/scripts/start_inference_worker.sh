#!/bin/bash
# Start the gpu/inference_worker process.
#
# Pre-loads BGE-large-zh on the assigned GPU and BLPOPs queue:inference.
# Default GPU = 7 (shared with Reactor; BGE only uses ~3 GB).
#
# Usage: bash gpu/scripts/start_inference_worker.sh
#
# Env knobs (all optional):
#   INFERENCE_GPU_ID    -- which CUDA device to expose (default: 7)
#   CONDA_ENV           -- conda env name (default: llm)
#   PROJECT_DIR         -- repo root (default: cwd two levels up)
#
# Logs:  /tmp/inference_worker.log
# PID:   /tmp/inference_worker.pid
# Stop:  bash gpu/scripts/stop_inference_worker.sh

set -e

INFERENCE_GPU_ID="${INFERENCE_GPU_ID:-7}"
CONDA_ENV="${VLLM_CONDA_ENV:-llm}"
CONDA_BIN="${CONDA_BIN:-/home/gime/soft/miniconda3/bin}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(dirname "$(dirname "$SCRIPT_DIR")")}"

LOG_FILE="/tmp/inference_worker.log"
PID_FILE="/tmp/inference_worker.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "inference_worker already running (PID $(cat "$PID_FILE")) — refusing to start a duplicate"
    exit 1
fi

# shellcheck disable=SC1091
source "$CONDA_BIN/activate" "$CONDA_ENV"

cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR:${PYTHONPATH:-}"

# When CUDA_VISIBLE_DEVICES restricts the worker to a single physical GPU,
# torch sees that card as cuda:0, so EMBEDDING_DEVICE should be "cuda".
CUDA_VISIBLE_DEVICES="$INFERENCE_GPU_ID" \
EMBEDDING_DEVICE="${EMBEDDING_DEVICE:-cuda}" \
nohup python -m gpu.inference_worker.main \
    > "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
echo "inference_worker starting on GPU $INFERENCE_GPU_ID"
echo "  PID:  $(cat "$PID_FILE")"
echo "  Log:  tail -f $LOG_FILE"
echo "  Stop: bash gpu/scripts/stop_inference_worker.sh"
