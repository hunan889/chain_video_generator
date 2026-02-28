#!/bin/bash
# Download Wan2.2 models from HuggingFace (Kijai/WanVideo_comfy)
# Uses aria2c for multi-connection download
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; source "$PROJECT_DIR/.env"; set +a
fi

COMFYUI_RAW="${COMFYUI_PATH:-./ComfyUI}"
if [[ "$COMFYUI_RAW" != /* ]]; then
    COMFYUI_DIR="$(cd "$PROJECT_DIR" && realpath -m "$COMFYUI_RAW")"
else
    COMFYUI_DIR="$COMFYUI_RAW"
fi

MODELS_DIR="$COMFYUI_DIR/models"
HF_BASE="https://huggingface.co/Kijai/WanVideo_comfy/resolve/main"

download() {
    local url="$1"
    local dest="$2"
    if [ -f "$dest" ]; then
        echo "[SKIP] $dest already exists"
        return
    fi
    echo "[DOWNLOAD] $dest"
    mkdir -p "$(dirname "$dest")"
    aria2c -x 8 -s 8 --max-tries=5 --retry-wait=3 \
        -d "$(dirname "$dest")" -o "$(basename "$dest")" \
        "$url"
}

echo "=== Downloading Wan2.2 Models ==="
echo "Models dir: $MODELS_DIR"

# A14B I2V HIGH/LOW
echo "--- A14B I2V High Noise (28.6GB) ---"
download "${HF_BASE}/Wan2_2-I2V-A14B-HIGH_bf16.safetensors" \
    "${MODELS_DIR}/diffusion_models/Wan2_2-I2V-A14B-HIGH_bf16.safetensors"

echo "--- A14B I2V Low Noise (28.6GB) ---"
download "${HF_BASE}/Wan2_2-I2V-A14B-LOW_bf16.safetensors" \
    "${MODELS_DIR}/diffusion_models/Wan2_2-I2V-A14B-LOW_bf16.safetensors"

# 5B TI2V Turbo
echo "--- 5B TI2V Turbo (10.2GB) ---"
download "${HF_BASE}/Wan22-Turbo/Wan2_2-TI2V-5B-Turbo_fp16.safetensors" \
    "${MODELS_DIR}/diffusion_models/Wan2_2-TI2V-5B-Turbo_fp16.safetensors"

# Text encoder
echo "--- UMT5-XXL Text Encoder fp8 (6.7GB) ---"
download "${HF_BASE}/umt5-xxl-enc-fp8_e4m3fn.safetensors" \
    "${MODELS_DIR}/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors"

# VAE models
echo "--- VAE 2.2 (1.4GB, for 5B) ---"
download "${HF_BASE}/Wan2_2_VAE_bf16.safetensors" \
    "${MODELS_DIR}/vae/Wan2_2_VAE_bf16.safetensors"

echo "--- VAE 2.1 (0.3GB, for A14B) ---"
download "${HF_BASE}/Wan2_1_VAE_bf16.safetensors" \
    "${MODELS_DIR}/vae/Wan2_1_VAE_bf16.safetensors"

echo ""
echo "=== Download complete ==="
echo "--- Diffusion models ---"
ls -lh ${MODELS_DIR}/diffusion_models/Wan2_2*.safetensors 2>/dev/null || echo "(none)"
echo "--- Text encoders ---"
ls -lh ${MODELS_DIR}/text_encoders/umt5*.safetensors 2>/dev/null || echo "(none)"
echo "--- VAE ---"
ls -lh ${MODELS_DIR}/vae/Wan2_*.safetensors 2>/dev/null || echo "(none)"
