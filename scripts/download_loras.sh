#!/bin/bash
# Download LoRA models from CivitAI
# Usage: bash scripts/download_loras.sh
# Set CIVITAI_API_TOKEN in .env or environment for authenticated downloads
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

LORAS_DIR="$COMFYUI_DIR/models/loras"
mkdir -p "$LORAS_DIR"

download_civitai() {
    local version_id="$1"
    local filename="$2"
    local dest="$LORAS_DIR/$filename"

    if [ -f "$dest" ]; then
        echo "[SKIP] $filename already exists"
        return
    fi

    local url="https://civitai.com/api/download/models/${version_id}"
    if [ -n "$CIVITAI_API_TOKEN" ]; then
        url="${url}?token=${CIVITAI_API_TOKEN}"
    fi

    echo "[DOWNLOAD] $filename (version $version_id)"
    aria2c -x 8 -s 8 --max-tries=5 --retry-wait=3 \
        -d "$LORAS_DIR" -o "$filename" "$url"

    # Verify file was downloaded
    if [ -f "$dest" ]; then
        local size
        size=$(stat -c%s "$dest" 2>/dev/null || stat -f%z "$dest" 2>/dev/null)
        echo "[OK] $filename (${size} bytes)"
    else
        echo "[FAIL] $filename download failed"
    fi
}

echo "=== Downloading LoRA Models ==="
echo "LoRA dir: $LORAS_DIR"
echo ""

if [ -z "$CIVITAI_API_TOKEN" ]; then
    echo "WARNING: CIVITAI_API_TOKEN not set. Some downloads may fail."
    echo "Set it in .env or: export CIVITAI_API_TOKEN=your_token"
    echo ""
fi
# ── Uncomment and fill in CivitAI model version IDs to download ──
# Find the version ID from the CivitAI model page URL:
#   https://civitai.com/models/XXXXX?modelVersionId=YYYYYY
#   The version ID is YYYYYY
#
# download_civitai "VERSION_ID" "WAN-2.2-I2V-FaceDownAssUp.safetensors"
# download_civitai "VERSION_ID" "WAN-2.2-I2V-Orgasm.safetensors"
# download_civitai "VERSION_ID" "WAN-2.2-I2V-BreastPlay.safetensors"
# download_civitai "VERSION_ID" "reverse_suspended_congress_I2V.safetensors"
# download_civitai "VERSION_ID" "WAN-2.2-I2V-Double-Blowjob.safetensors"
# download_civitai "VERSION_ID" "WAN-2.2-I2V-POV-Titfuck-Paizuri.safetensors"
# download_civitai "VERSION_ID" "WAN-2.2-I2V-HandjobBlowjobCombo.safetensors"
# download_civitai "VERSION_ID" "WAN-2.2-I2V-POV-Cowgirl.safetensors"
# download_civitai "VERSION_ID" "big_breasts_v2_epoch_30.safetensors"
# download_civitai "VERSION_ID" "Instagirlv2.safetensors"

echo ""
echo "=== LoRA download complete ==="
ls -lh "$LORAS_DIR"/*.safetensors 2>/dev/null || echo "(no LoRA files found)"
