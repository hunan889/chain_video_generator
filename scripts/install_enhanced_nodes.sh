#!/bin/bash
# Install KJNodes custom node for ColorMatch support
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

source "$PROJECT_DIR/.env" 2>/dev/null || true

COMFYUI_PATH="${COMFYUI_PATH:-/home/gime/soft/ComfyUI}"
CUSTOM_NODES="$COMFYUI_PATH/custom_nodes"

echo "Installing KJNodes to $CUSTOM_NODES..."

if [ -d "$CUSTOM_NODES/ComfyUI-KJNodes" ]; then
    echo "KJNodes already installed, pulling latest..."
    cd "$CUSTOM_NODES/ComfyUI-KJNodes"
    git pull
else
    cd "$CUSTOM_NODES"
    git clone https://github.com/kijai/ComfyUI-KJNodes.git
fi

if [ -f "$CUSTOM_NODES/ComfyUI-KJNodes/requirements.txt" ]; then
    echo "Installing KJNodes requirements..."
    VENV_PIP="$COMFYUI_PATH/venv/bin/pip"
    if [ -x "$VENV_PIP" ]; then
        "$VENV_PIP" install -r "$CUSTOM_NODES/ComfyUI-KJNodes/requirements.txt"
    else
        pip install -r "$CUSTOM_NODES/ComfyUI-KJNodes/requirements.txt"
    fi
fi

echo "KJNodes installed successfully."
echo "Please restart ComfyUI for changes to take effect."
