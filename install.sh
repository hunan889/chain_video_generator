#!/bin/bash
set -e

# Wan2.2 Video Service - 一键安装脚本
# 用途：自动安装 ComfyUI、下载模型、配置环境

echo "=========================================="
echo "Wan2.2 Video Service - 一键安装"
echo "=========================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查是否为 root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}请使用 root 权限运行此脚本${NC}"
    echo "使用: sudo bash install.sh"
    exit 1
fi

# 获取当前目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"

echo -e "${GREEN}项目目录: $SCRIPT_DIR${NC}"
echo -e "${GREEN}父目录: $PARENT_DIR${NC}"

# 配置变量
COMFYUI_DIR="${PARENT_DIR}/ComfyUI"
PYTHON_VERSION="3.11"

# 步骤 1: 检查系统依赖
echo ""
echo "=========================================="
echo "步骤 1: 检查系统依赖"
echo "=========================================="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python3 未安装${NC}"
    echo "正在安装 Python ${PYTHON_VERSION}..."
    apt update
    apt install -y python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python3-pip
else
    PYTHON_VER=$(python3 --version | cut -d' ' -f2)
    echo -e "${GREEN}✓ Python 已安装: ${PYTHON_VER}${NC}"
fi

# 检查 Git
if ! command -v git &> /dev/null; then
    echo -e "${YELLOW}Git 未安装，正在安装...${NC}"
    apt install -y git
else
    echo -e "${GREEN}✓ Git 已安装${NC}"
fi

# 检查 ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo -e "${YELLOW}ffmpeg 未安装，正在安装...${NC}"
    apt install -y ffmpeg
else
    echo -e "${GREEN}✓ ffmpeg 已安装${NC}"
fi

# 检查 Redis
if ! command -v redis-server &> /dev/null; then
    echo -e "${YELLOW}Redis 未安装，正在安装...${NC}"
    apt install -y redis-server
    systemctl enable redis-server
    systemctl start redis-server
else
    echo -e "${GREEN}✓ Redis 已安装${NC}"
fi

# 检查 CUDA (可选)
if command -v nvidia-smi &> /dev/null; then
    echo -e "${GREEN}✓ NVIDIA GPU 已检测到${NC}"
    nvidia-smi --query-gpu=name --format=csv,noheader
else
    echo -e "${YELLOW}⚠ 未检测到 NVIDIA GPU，将使用 CPU 模式${NC}"
fi

# 步骤 2: 克隆/更新 ComfyUI
echo ""
echo "=========================================="
echo "步骤 2: 安装 ComfyUI"
echo "=========================================="

if [ -d "$COMFYUI_DIR" ]; then
    echo -e "${YELLOW}ComfyUI 目录已存在，跳过克隆${NC}"
else
    echo "克隆 ComfyUI..."
    cd "$PARENT_DIR"
    git clone https://github.com/comfyanonymous/ComfyUI.git
    echo -e "${GREEN}✓ ComfyUI 克隆完成${NC}"
fi

# 步骤 3: 创建 Python 虚拟环境
echo ""
echo "=========================================="
echo "步骤 3: 创建 Python 虚拟环境"
echo "=========================================="

cd "$COMFYUI_DIR"

if [ -d "venv" ]; then
    echo -e "${YELLOW}虚拟环境已存在，跳过创建${NC}"
else
    echo "创建虚拟环境..."
    python3 -m venv venv
    echo -e "${GREEN}✓ 虚拟环境创建完成${NC}"
fi

# 激活虚拟环境
source venv/bin/activate

# 步骤 4: 安装 ComfyUI 依赖
echo ""
echo "=========================================="
echo "步骤 4: 安装 ComfyUI 依赖"
echo "=========================================="

echo "安装 PyTorch..."
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

echo "安装 ComfyUI 依赖..."
pip install -r requirements.txt

echo -e "${GREEN}✓ ComfyUI 依赖安装完成${NC}"

# 步骤 5: 安装自定义节点
echo ""
echo "=========================================="
echo "步骤 5: 安装自定义节点"
echo "=========================================="

cd "$COMFYUI_DIR/custom_nodes"

# ComfyUI-Manager
if [ -d "ComfyUI-Manager" ]; then
    echo -e "${YELLOW}ComfyUI-Manager 已存在${NC}"
else
    echo "安装 ComfyUI-Manager..."
    git clone https://github.com/ltdrdata/ComfyUI-Manager.git
    cd ComfyUI-Manager
    pip install -r requirements.txt
    cd ..
fi

# ComfyUI-WanVideoWrapper
if [ -d "ComfyUI-WanVideoWrapper" ]; then
    echo -e "${YELLOW}ComfyUI-WanVideoWrapper 已存在${NC}"
else
    echo "安装 ComfyUI-WanVideoWrapper..."
    git clone https://github.com/kijai/ComfyUI-WanVideoWrapper.git
    cd ComfyUI-WanVideoWrapper
    pip install -r requirements.txt
    cd ..
fi

# ComfyUI-VideoHelperSuite
if [ -d "ComfyUI-VideoHelperSuite" ]; then
    echo -e "${YELLOW}ComfyUI-VideoHelperSuite 已存在${NC}"
else
    echo "安装 ComfyUI-VideoHelperSuite..."
    git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git
    cd ComfyUI-VideoHelperSuite
    pip install -r requirements.txt
    cd ..
fi

# rgthree-comfy
if [ -d "rgthree-comfy" ]; then
    echo -e "${YELLOW}rgthree-comfy 已存在${NC}"
else
    echo "安装 rgthree-comfy..."
    git clone https://github.com/rgthree/rgthree-comfy.git
fi

echo -e "${GREEN}✓ 自定义节点安装完成${NC}"

# 步骤 6: 下载模型
echo ""
echo "=========================================="
echo "步骤 6: 下载模型"
echo "=========================================="

cd "$SCRIPT_DIR"

echo "下载 Wan2.2 模型..."
bash scripts/download_models.sh

echo -e "${GREEN}✓ 模型下载完成${NC}"

# 步骤 7: 安装 API 服务依赖
echo ""
echo "=========================================="
echo "步骤 7: 安装 API 服务依赖"
echo "=========================================="

cd "$SCRIPT_DIR"

# 创建虚拟环境（如果不存在）
if [ -d "venv" ]; then
    echo -e "${YELLOW}API 虚拟环境已存在${NC}"
else
    echo "创建 API 虚拟环境..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "安装 API 依赖..."
pip install -r requirements.txt

echo -e "${GREEN}✓ API 依赖安装完成${NC}"

# 步骤 8: 配置环境变量
echo ""
echo "=========================================="
echo "步骤 8: 配置环境变量"
echo "=========================================="

if [ ! -f ".env" ]; then
    echo "创建 .env 文件..."
    cp .env.example .env
    echo -e "${GREEN}✓ .env 文件已创建${NC}"
    echo -e "${YELLOW}请编辑 .env 文件配置您的参数${NC}"
else
    echo -e "${YELLOW}.env 文件已存在，跳过创建${NC}"
fi

# 步骤 9: 创建必要的目录
echo ""
echo "=========================================="
echo "步骤 9: 创建必要的目录"
echo "=========================================="

mkdir -p storage/videos
mkdir -p storage/uploads
mkdir -p logs

echo -e "${GREEN}✓ 目录创建完成${NC}"

# 完成
echo ""
echo "=========================================="
echo "安装完成！"
echo "=========================================="
echo ""
echo "下一步操作："
echo "1. 编辑配置文件: nano .env"
echo "2. 启动所有服务: bash scripts/start_all.sh"
echo "3. 查看服务状态:"
echo "   - ComfyUI A14B: screen -r comfyui_a14b"
echo "   - API 服务: screen -r wan22_api"
echo "4. 访问 Web 界面: http://localhost:8000"
echo ""
echo "停止服务: bash scripts/stop_all.sh"
echo ""
echo -e "${GREEN}祝您使用愉快！${NC}"
