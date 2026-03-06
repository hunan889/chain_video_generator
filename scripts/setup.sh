#!/bin/bash
# One-click setup for Wan2.2 Video Generation Service
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Wan2.2 Video Generation Service Setup ==="
echo "Project dir: $PROJECT_DIR"

# ── 1. System dependencies ──────────────────────────────────────────
echo "--- Installing system dependencies ---"
apt-get update -qq
apt-get install -y -qq redis-server ffmpeg aria2 screen wget git

systemctl enable redis-server
systemctl start redis-server
echo "Redis: $(redis-cli ping)"

# ── 2. Generate .env from template if missing ────────────────────────
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "--- Generating .env from .env.example ---"
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "Created .env — edit it to configure GPU IDs and ports"
fi

# Load .env
set -a; source "$PROJECT_DIR/.env"; set +a

# ── 3. Resolve ComfyUI path ─────────────────────────────────────────
COMFYUI_RAW="${COMFYUI_PATH:-./ComfyUI}"
if [[ "$COMFYUI_RAW" != /* ]]; then
    COMFYUI_DIR="$(cd "$PROJECT_DIR" && realpath -m "$COMFYUI_RAW")"
else
    COMFYUI_DIR="$COMFYUI_RAW"
fi

# ── 4. Clone ComfyUI if not present ─────────────────────────────────
if [ ! -d "$COMFYUI_DIR" ]; then
    echo "--- Cloning ComfyUI ---"
    git clone https://github.com/comfyanonymous/ComfyUI.git "$COMFYUI_DIR"
else
    echo "[SKIP] ComfyUI already exists at $COMFYUI_DIR"
fi

# ── 5. Create venv & install PyTorch ─────────────────────────────────
if [ ! -d "$COMFYUI_DIR/venv" ]; then
    echo "--- Creating Python venv ---"
    python3 -m venv "$COMFYUI_DIR/venv"
fi

echo "--- Installing PyTorch (CUDA 12.6) ---"
"$COMFYUI_DIR/venv/bin/pip" install -q --upgrade pip
"$COMFYUI_DIR/venv/bin/pip" install -q torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu126

echo "--- Installing ComfyUI requirements ---"
"$COMFYUI_DIR/venv/bin/pip" install -q -r "$COMFYUI_DIR/requirements.txt"

# ── 6. Install custom nodes ──────────────────────────────────────────
echo "--- Installing ComfyUI custom nodes ---"
mkdir -p "$COMFYUI_DIR/custom_nodes"
cd "$COMFYUI_DIR/custom_nodes"

# Core nodes (video generation pipeline)
CORE_NODES=(
    "https://github.com/kijai/ComfyUI-WanVideoWrapper.git"
    "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git"
    "https://github.com/princepainter/ComfyUI-PainterI2V.git"
    "https://github.com/princepainter/ComfyUI-PainterLongVideo.git"
    "https://github.com/stduhpf/ComfyUI-WanMoeKSampler.git"
    "https://github.com/LAOGOU-666/Comfyui-Memory_Cleanup.git"
    "https://github.com/rgthree/rgthree-comfy.git"
    "https://github.com/ashtar1984/comfyui-find-perfect-resolution.git"
    "https://github.com/ComfyAssets/ComfyUI_Selectors"
    "https://github.com/kijai/ComfyUI-KJNodes.git"
)

# TensorRT acceleration nodes (upscale + frame interpolation)
TRT_NODES=(
    "https://github.com/huchukato/ComfyUI-RIFE-TensorRT-Auto.git"
    "https://github.com/yuvraj108c/ComfyUI-Upscaler-Tensorrt"
)

# Optional utility nodes
UTIL_NODES=(
    "https://github.com/pollockjj/ComfyUI-MultiGPU.git"
    "https://github.com/ltdrdata/ComfyUI-Manager.git"
    "https://github.com/city96/ComfyUI-GGUF.git"
    "https://github.com/WASasquatch/was-node-suite-comfyui.git"
    "https://github.com/yolain/ComfyUI-Easy-Use.git"
    "https://github.com/kijai/ComfyUI-MMAudio.git"
    "https://github.com/1038lab/ComfyUI-QwenVL.git"
)

for repo in "${CORE_NODES[@]}" "${TRT_NODES[@]}" "${UTIL_NODES[@]}"; do
    dir=$(basename "$repo" .git)
    if [ ! -d "$dir" ]; then
        echo "Cloning $dir ..."
        git clone --depth 1 "$repo"
    else
        echo "[SKIP] $dir already exists"
    fi
done

# Install node dependencies
echo "--- Installing custom node dependencies ---"
for dir in */; do
    if [ -f "$dir/requirements.txt" ]; then
        echo "  Installing deps for $dir"
        "$COMFYUI_DIR/venv/bin/pip" install -q -r "$dir/requirements.txt" 2>/dev/null || true
    fi
done

# ── 7. Install API dependencies ──────────────────────────────────────
echo "--- Installing API dependencies ---"
pip install -q -r "$PROJECT_DIR/requirements.txt"

# ── 8. Create storage directories ────────────────────────────────────
mkdir -p "$PROJECT_DIR/storage/videos" "$PROJECT_DIR/storage/uploads"

# ── 9. Install Claude Code CLI ───────────────────────────────────────
if [ -f "$PROJECT_DIR/tools/claude" ]; then
    echo "--- Installing Claude Code CLI ---"
    ln -sf "$PROJECT_DIR/tools/claude" /usr/local/bin/claude
    echo "Claude Code $(claude --version) installed to /usr/local/bin/claude"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. vim .env                        # Configure GPU IDs and ports"
echo "  2. bash scripts/download_models.sh # Download models (~75GB)"
echo "  3. bash scripts/download_loras.sh  # Download LoRAs (optional)"
echo "  4. bash scripts/start_all.sh       # Start all services"
