#!/bin/bash
# Download standard T2V models for Wan 2.2

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load environment variables
if [ -f "$PROJECT_DIR/.env" ]; then
    source "$PROJECT_DIR/.env"
fi

# Resolve ComfyUI path
COMFYUI_PATH="${COMFYUI_PATH:-$PROJECT_DIR/../ComfyUI}"
MODELS_DIR="$COMFYUI_PATH/models/diffusion_models"

echo "=================================================="
echo "Downloading Wan 2.2 T2V Standard Models"
echo "=================================================="
echo "Target directory: $MODELS_DIR"
echo ""

mkdir -p "$MODELS_DIR"
cd "$MODELS_DIR"

# Model URLs from CivitAI
HIGH_URL="https://civitai.com/api/download/models/2157100"  # HIGH noise version
LOW_URL="https://civitai.com/api/download/models/2157099"   # LOW noise version

HIGH_FILE="wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
LOW_FILE="wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"

# Download HIGH noise model
if [ -f "$HIGH_FILE" ]; then
    echo "✓ $HIGH_FILE already exists, skipping..."
else
    echo "Downloading HIGH noise model (13.31 GB)..."
    echo "URL: $HIGH_URL"
    if [ -n "$CIVITAI_API_TOKEN" ]; then
        curl -L -o "$HIGH_FILE" -H "Authorization: Bearer $CIVITAI_API_TOKEN" "$HIGH_URL"
    else
        curl -L -o "$HIGH_FILE" "$HIGH_URL"
    fi
    echo "✓ Downloaded $HIGH_FILE"
fi

echo ""

# Download LOW noise model
if [ -f "$LOW_FILE" ]; then
    echo "✓ $LOW_FILE already exists, skipping..."
else
    echo "Downloading LOW noise model (13.31 GB)..."
    echo "URL: $LOW_URL"
    if [ -n "$CIVITAI_API_TOKEN" ]; then
        curl -L -o "$LOW_FILE" -H "Authorization: Bearer $CIVITAI_API_TOKEN" "$LOW_URL"
    else
        curl -L -o "$LOW_FILE" "$LOW_URL"
    fi
    echo "✓ Downloaded $LOW_FILE"
fi

echo ""
echo "=================================================="
echo "Download Complete!"
echo "=================================================="
echo ""
echo "Models installed:"
ls -lh "$MODELS_DIR" | grep "wan2.2_t2v"
echo ""
echo "Usage:"
echo "  - Use 't2v_standard' preset in T2V mode"
echo "  - Combine with NSFW LoRAs for adult content generation"
echo ""
