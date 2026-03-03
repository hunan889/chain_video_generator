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

MIN_MODEL_SIZE=1048576  # 1MB — any valid safetensors is larger than this
PYTHON="${COMFYUI_DIR}/venv/bin/python"

validate_file() {
    local file="$1"
    if [ ! -f "$file" ]; then
        return 1
    fi
    local size
    size=$(stat -c%s "$file" 2>/dev/null || echo 0)
    if [ "$size" -lt "$MIN_MODEL_SIZE" ]; then
        echo "[INVALID] $file is too small (${size} bytes), removing"
        rm -f "$file"
        return 1
    fi
    # safetensors integrity check: verify header declares size matching actual file
    if ! "$PYTHON" -c "
import struct, json, sys
f = open(sys.argv[1], 'rb')
header_len = struct.unpack('<Q', f.read(8))[0]
meta = json.loads(f.read(header_len))
expected = 8 + header_len
for v in meta.values():
    if isinstance(v, dict) and 'data_offsets' in v:
        end = v['data_offsets'][1]
        if end > expected - 8 - header_len:
            expected = 8 + header_len + end
actual = f.seek(0, 2)
if actual < expected:
    sys.exit(1)
" "$file" 2>/dev/null; then
        echo "[CORRUPT] $file incomplete (truncated), removing"
        rm -f "$file"
        return 1
    fi
    return 0
}

download() {
    local url="$1"
    local dest="$2"
    if validate_file "$dest"; then
        echo "[SKIP] $dest already exists"
        return
    fi
    echo "[DOWNLOAD] $dest"
    mkdir -p "$(dirname "$dest")"
    aria2c -x 8 -s 8 --max-tries=5 --retry-wait=3 \
        -d "$(dirname "$dest")" -o "$(basename "$dest")" \
        "$url"
    if ! validate_file "$dest"; then
        echo "[ERROR] Failed to download valid file: $dest"
        return 1
    fi
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

# Text encoder (standard)
echo "--- UMT5-XXL Text Encoder fp8 (6.7GB) ---"
download "${HF_BASE}/umt5-xxl-enc-fp8_e4m3fn.safetensors" \
    "${MODELS_DIR}/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors"

# Text encoder (NSFW optimized) — bf16 version (fp8_scaled not supported by WanVideoWrapper)
HF_NSFW_T5="https://huggingface.co/NSFW-API/NSFW-Wan-UMT5-XXL/resolve/main"
echo "--- NSFW UMT5-XXL Text Encoder bf16 (~9.5GB) ---"
download "${HF_NSFW_T5}/nsfw_wan_umt5-xxl_bf16.safetensors" \
    "${MODELS_DIR}/text_encoders/nsfw_wan_umt5-xxl_bf16.safetensors"

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
ls -lh ${MODELS_DIR}/text_encoders/*umt5*.safetensors 2>/dev/null || echo "(none)"
echo "--- VAE ---"
ls -lh ${MODELS_DIR}/vae/Wan2_*.safetensors 2>/dev/null || echo "(none)"
