#!/bin/bash
# 从5090机器反向同步回本地
# 同步所有之前推送过去的内容（如果有变更）

set -e

TARGET_HOST="192.168.16.7"
TARGET_USER="root"
LOG_FILE="/tmp/sync_from_5090_$(date +%Y%m%d_%H%M%S).log"

# 颜色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=========================================="
echo "从5090反向同步工具"
echo "==========================================${NC}"
echo -e "${GREEN}源:${NC} $TARGET_USER@$TARGET_HOST"
echo -e "${GREEN}目标:${NC} 本地"
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

# 显示同步计划
echo -e "${BLUE}=========================================="
echo "同步计划 (远程 → 本地):"
echo "==========================================${NC}"
echo "1. wan22-service (API服务代码)"
echo "2. ComfyUI (包含自定义节点)"
echo "3. ComfyUI模型文件"
echo "4. Claude 配置文件"
echo ""
echo -e "${YELLOW}注意: 这将用远程的变更覆盖本地文件${NC}"
echo ""

# 检查是否有 -y 参数自动确认
AUTO_YES=false
if [[ "$1" == "-y" ]] || [[ "$1" == "--yes" ]]; then
    AUTO_YES=true
fi

if [ "$AUTO_YES" = false ]; then
    read -p "是否开始同步? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "已取消"
        exit 0
    fi
else
    echo "自动确认模式，开始同步..."
fi

echo "" | tee -a $LOG_FILE
echo "==========================================" | tee -a $LOG_FILE
echo "开始反向同步 - $(date '+%Y-%m-%d %H:%M:%S')" | tee -a $LOG_FILE
echo "==========================================" | tee -a $LOG_FILE

# 1. 同步 wan22-service
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[1/4] 同步 wan22-service...${NC}" | tee -a $LOG_FILE
rsync -avz --progress \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.git/' \
    --exclude='storage/videos/' \
    --exclude='storage/uploads/' \
    --exclude='*.log' \
    $TARGET_USER@$TARGET_HOST:/home/gime/soft/wan22-service/ \
    /home/gime/soft/wan22-service/ 2>&1 | tee -a $LOG_FILE

# 2. 同步 ComfyUI (包含自定义节点)
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[2/4] 同步 ComfyUI 和自定义节点...${NC}" | tee -a $LOG_FILE
rsync -avz --progress \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.git/' \
    --exclude='models/' \
    --exclude='output/' \
    --exclude='input/' \
    --exclude='temp/' \
    $TARGET_USER@$TARGET_HOST:/home/gime/soft/ComfyUI/ \
    /home/gime/soft/ComfyUI/ 2>&1 | tee -a $LOG_FILE

# 3. 同步 ComfyUI 模型 (增量)
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[3/4] 同步 ComfyUI 模型文件...${NC}" | tee -a $LOG_FILE
rsync -avz --progress \
    --exclude='*.tmp' \
    --exclude='*.lock' \
    $TARGET_USER@$TARGET_HOST:/data/ComfyUI/models/ \
    /home/gime/soft/ComfyUI/models/ 2>&1 | tee -a $LOG_FILE

# 4. 同步 Claude 配置
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[4/4] 同步 Claude 配置文件...${NC}" | tee -a $LOG_FILE
rsync -avz --progress \
    --exclude='cache/' \
    --exclude='*.log' \
    --exclude='projects/' \
    $TARGET_USER@$TARGET_HOST:~/.claude/ \
    $HOME/.claude/ 2>&1 | tee -a $LOG_FILE

# 显示本地状态
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}=========================================="
echo "本地状态"
echo "==========================================${NC}"
echo "磁盘使用:"
df -h /home/gime/soft | tail -1
echo ""
echo "目录大小:"
du -sh /home/gime/soft/wan22-service 2>/dev/null || echo "wan22-service: 未找到"
du -sh /home/gime/soft/ComfyUI 2>/dev/null || echo "ComfyUI: 未找到"
du -sh /home/gime/soft/ComfyUI/models 2>/dev/null || echo "models: 未找到"

echo "" | tee -a $LOG_FILE
echo -e "${GREEN}=========================================="
echo "✓ 反向同步完成！"
echo "==========================================${NC}"
echo -e "${GREEN}日志文件:${NC} $LOG_FILE"
echo ""
echo -e "${YELLOW}提示:${NC}"
echo "- 如果代码有变更，记得重启服务"
echo "- 如果配置文件有变更，检查 .env 和 config/"
