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

# NSFW model presets (fp8 quantized)
HF_NSFW_FP8="https://huggingface.co/NSFW-API/NSFW-WAN2.2-I2V-A14B-FP8/resolve/main"
echo "--- NSFW A14B HIGH fp8 (14.3GB) ---"
download "${HF_NSFW_FP8}/wan22EnhancedNSFWSVICamera_nsfwV2FP8H.safetensors" \
    "${MODELS_DIR}/diffusion_models/wan22EnhancedNSFWSVICamera_nsfwV2FP8H.safetensors"

echo "--- NSFW A14B LOW fp8 (14.3GB) ---"
download "${HF_NSFW_FP8}/wan22EnhancedNSFWSVICamera_nsfwV2FP8L.safetensors" \
    "${MODELS_DIR}/diffusion_models/wan22EnhancedNSFWSVICamera_nsfwV2FP8L.safetensors"

# CLIP model for Story Mode
HF_NSFW_CLIP="https://huggingface.co/NSFW-API/NSFW-WAN-CLIP/resolve/main"
echo "--- NSFW CLIP (2.4GB) ---"
download "${HF_NSFW_CLIP}/nsfw_wan_clip_bf16.safetensors" \
    "${MODELS_DIR}/clip/nsfw_wan_clip_bf16.safetensors"

# ── MMAudio models ──────────────────────────────────────────────────
echo ""
echo "=== Downloading MMAudio Models ==="
MMAUDIO_DIR="${MODELS_DIR}/mmaudio"
mkdir -p "$MMAUDIO_DIR"

HF_MMAUDIO_NSFW="https://huggingface.co/phazei/NSFW_MMaudio/resolve/main"
HF_MMAUDIO="https://huggingface.co/Kijai/MMAudio_safetensors/resolve/main"

echo "--- MMAudio main model (2.0GB) ---"
download "${HF_MMAUDIO_NSFW}/mmaudio_large_44k_nsfw_gold_8.5k_final_fp16.safetensors" \
    "${MMAUDIO_DIR}/mmaudio_large_44k_nsfw_gold_8.5k_final_fp16.safetensors"

echo "--- MMAudio VAE (583MB) ---"
download "${HF_MMAUDIO}/mmaudio_vae_44k_fp16.safetensors" \
    "${MMAUDIO_DIR}/mmaudio_vae_44k_fp16.safetensors"

echo "--- MMAudio Synchformer (453MB) ---"
download "${HF_MMAUDIO}/mmaudio_synchformer_fp16.safetensors" \
    "${MMAUDIO_DIR}/mmaudio_synchformer_fp16.safetensors"

echo "--- MMAudio CLIP (1.9GB) ---"
download "${HF_MMAUDIO}/apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors" \
    "${MMAUDIO_DIR}/apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors"

# Pre-download BigVGAN vocoder (auto-downloaded by MMAudio on first run)
echo "--- BigVGAN vocoder (pre-cache) ---"
if "$PYTHON" -c "from huggingface_hub import snapshot_download; snapshot_download('nvidia/bigvgan_v2_44khz_128band_512x')" 2>/dev/null; then
    echo "[OK] BigVGAN vocoder cached"
else
    echo "[WARN] BigVGAN pre-download failed (will download on first MMAudio run)"
fi

# ── Upscale model ───────────────────────────────────────────────────
echo ""
echo "=== Downloading Upscale Model ==="
UPSCALE_DIR="${MODELS_DIR}/upscale_models"
mkdir -p "$UPSCALE_DIR"

echo "--- 4x-UltraSharp (67MB) ---"
download "https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth" \
    "${UPSCALE_DIR}/4x-UltraSharp.pth"

echo ""
echo "=== Download complete ==="
echo "--- Diffusion models ---"
ls -lh ${MODELS_DIR}/diffusion_models/Wan2_2*.safetensors ${MODELS_DIR}/diffusion_models/wan22*.safetensors 2>/dev/null || echo "(none)"
echo "--- Text encoders ---"
ls -lh ${MODELS_DIR}/text_encoders/*umt5*.safetensors 2>/dev/null || echo "(none)"
echo "--- CLIP ---"
ls -lh ${MODELS_DIR}/clip/*.safetensors 2>/dev/null || echo "(none)"
echo "--- VAE ---"
ls -lh ${MODELS_DIR}/vae/Wan2_*.safetensors 2>/dev/null || echo "(none)"
echo "--- MMAudio ---"
ls -lh ${MMAUDIO_DIR}/*.safetensors 2>/dev/null || echo "(none)"
echo "--- Upscale ---"
ls -lh ${UPSCALE_DIR}/*.pth 2>/dev/null || echo "(none)"
