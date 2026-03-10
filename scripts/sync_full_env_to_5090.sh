#!/bin/bash
# 完整环境同步脚本 - 同步所有相关文件到5090机器
# 包括: wan22-service, ComfyUI, 自定义节点, Claude CLI, 配置文件

set -e

TARGET_HOST="192.168.16.7"
TARGET_USER="root"
LOG_FILE="/tmp/full_sync_$(date +%Y%m%d_%H%M%S).log"

# 颜色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=========================================="
echo "完整环境同步工具"
echo "==========================================${NC}"
echo -e "${GREEN}目标:${NC} $TARGET_USER@$TARGET_HOST"
echo -e "${GREEN}日志:${NC} $LOG_FILE"
echo ""

# 检查SSH连接
echo -e "${YELLOW}检查SSH连接...${NC}"
if ! ssh -o ConnectTimeout=5 $TARGET_USER@$TARGET_HOST "echo 'SSH连接成功'" > /dev/null 2>&1; then
    echo -e "${RED}错误: 无法连接到目标机器${NC}"
    exit 1
fi
echo -e "${GREEN}✓ SSH连接正常${NC}"
echo ""

# 同步项目列表
declare -A SYNC_ITEMS=(
    ["wan22-service"]="/home/gime/soft/wan22-service"
    ["ComfyUI"]="/home/gime/soft/ComfyUI"
    ["Claude CLI"]="$HOME/.local/bin/claude"
    ["Claude配置"]="$HOME/.claude"
)

# 显示同步计划
echo -e "${BLUE}=========================================="
echo "同步计划:"
echo "==========================================${NC}"
echo "1. wan22-service (API服务)"
echo "2. ComfyUI (包含自定义节点)"
echo "3. ComfyUI模型文件"
echo "4. Claude CLI 二进制"
echo "5. Claude 配置文件"
echo ""

read -p "是否开始同步? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

echo "" | tee -a $LOG_FILE
echo "=========================================="  | tee -a $LOG_FILE
echo "开始同步 - $(date '+%Y-%m-%d %H:%M:%S')" | tee -a $LOG_FILE
echo "==========================================" | tee -a $LOG_FILE

# 1. 同步 wan22-service
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[1/5] 同步 wan22-service...${NC}" | tee -a $LOG_FILE
rsync -avz --progress \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.git/' \
    --exclude='storage/videos/' \
    --exclude='storage/uploads/' \
    --exclude='*.log' \
    --exclude='.env' \
    /home/gime/soft/wan22-service/ \
    $TARGET_USER@$TARGET_HOST:/home/gime/soft/wan22-service/ 2>&1 | tee -a $LOG_FILE

# 2. 同步 ComfyUI (包含自定义节点)
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[2/5] 同步 ComfyUI 和自定义节点...${NC}" | tee -a $LOG_FILE
rsync -avz --progress \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.git/' \
    --exclude='models/' \
    --exclude='output/' \
    --exclude='input/' \
    --exclude='temp/' \
    /home/gime/soft/ComfyUI/ \
    $TARGET_USER@$TARGET_HOST:/home/gime/soft/ComfyUI/ 2>&1 | tee -a $LOG_FILE

# 3. 同步 ComfyUI 模型 (增量)
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[3/5] 同步 ComfyUI 模型文件...${NC}" | tee -a $LOG_FILE
rsync -avz --progress \
    --exclude='*.tmp' \
    --exclude='*.lock' \
    /home/gime/soft/ComfyUI/models/ \
    $TARGET_USER@$TARGET_HOST:/data/ComfyUI/models/ 2>&1 | tee -a $LOG_FILE

# 创建软链接
ssh $TARGET_USER@$TARGET_HOST "
    if [ ! -L /home/gime/soft/ComfyUI/models ]; then
        rm -rf /home/gime/soft/ComfyUI/models
        ln -sf /data/ComfyUI/models /home/gime/soft/ComfyUI/models
        echo '✓ 创建模型目录软链接'
    fi
" 2>&1 | tee -a $LOG_FILE

# 4. 同步 Claude CLI
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[4/5] 同步 Claude CLI 二进制...${NC}" | tee -a $LOG_FILE
if [ -f "$HOME/.local/bin/claude" ]; then
    ssh $TARGET_USER@$TARGET_HOST "mkdir -p ~/.local/bin"
    rsync -avz --progress \
        $HOME/.local/bin/claude \
        $TARGET_USER@$TARGET_HOST:~/.local/bin/ 2>&1 | tee -a $LOG_FILE
    ssh $TARGET_USER@$TARGET_HOST "chmod +x ~/.local/bin/claude"
    echo -e "${GREEN}✓ Claude CLI 同步完成${NC}" | tee -a $LOG_FILE
else
    echo -e "${YELLOW}⚠ Claude CLI 未找到，跳过${NC}" | tee -a $LOG_FILE
fi

# 5. 同步 Claude 配置
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[5/5] 同步 Claude 配置文件...${NC}" | tee -a $LOG_FILE
if [ -d "$HOME/.claude" ]; then
    rsync -avz --progress \
        --exclude='cache/' \
        --exclude='*.log' \
        --exclude='projects/' \
        $HOME/.claude/ \
        $TARGET_USER@$TARGET_HOST:~/.claude/ 2>&1 | tee -a $LOG_FILE
    echo -e "${GREEN}✓ Claude 配置同步完成${NC}" | tee -a $LOG_FILE
else
    echo -e "${YELLOW}⚠ Claude 配置目录未找到，跳过${NC}" | tee -a $LOG_FILE
fi

# 同步 .env.example (不覆盖 .env)
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}同步配置模板...${NC}" | tee -a $LOG_FILE
if [ -f "/home/gime/soft/wan22-service/.env.example" ]; then
    rsync -avz \
        /home/gime/soft/wan22-service/.env.example \
        $TARGET_USER@$TARGET_HOST:/home/gime/soft/wan22-service/ 2>&1 | tee -a $LOG_FILE
fi

# 显示目标机器状态
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}=========================================="
echo "目标机器状态"
echo "==========================================${NC}"
ssh $TARGET_USER@$TARGET_HOST "
    echo '磁盘使用:'
    df -h /data | tail -1
    echo ''
    echo '目录大小:'
    du -sh /home/gime/soft/wan22-service 2>/dev/null || echo 'wan22-service: 未找到'
    du -sh /home/gime/soft/ComfyUI 2>/dev/null || echo 'ComfyUI: 未找到'
    du -sh /data/ComfyUI/models 2>/dev/null || echo 'models: 未找到'
    echo ''
    echo 'Claude CLI:'
    ~/.local/bin/claude --version 2>/dev/null || echo 'Claude CLI: 未安装'
" 2>&1 | tee -a $LOG_FILE

echo "" | tee -a $LOG_FILE
echo -e "${GREEN}=========================================="
echo "✓ 同步完成！"
echo "==========================================${NC}"
echo -e "${GREEN}日志文件:${NC} $LOG_FILE"
echo ""
echo -e "${YELLOW}后续步骤:${NC}"
echo "1. 在目标机器上配置 .env 文件"
echo "2. 安装Python依赖: cd /home/gime/soft/wan22-service && pip install -r requirements.txt"
echo "3. 安装ComfyUI依赖: cd /home/gime/soft/ComfyUI && pip install -r requirements.txt"
echo "4. 启动服务: bash /home/gime/soft/wan22-service/scripts/start_all.sh"
