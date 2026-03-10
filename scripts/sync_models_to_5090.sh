#!/bin/bash
# 一键同步ComfyUI模型到5090机器
# 支持增量同步，只传输新增或修改的文件

set -e

# 配置
SOURCE_PATH="/home/gime/soft/ComfyUI/models"
TARGET_HOST="192.168.16.7"
TARGET_USER="root"
TARGET_PATH="/data/ComfyUI/models"
LOG_FILE="/tmp/model_sync_$(date +%Y%m%d_%H%M%S).log"

# 颜色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}=========================================="
echo "ComfyUI模型同步工具"
echo "==========================================${NC}"
echo -e "${GREEN}源路径:${NC} $SOURCE_PATH"
echo -e "${GREEN}目标:${NC} $TARGET_USER@$TARGET_HOST:$TARGET_PATH"
echo -e "${GREEN}日志:${NC} $LOG_FILE"
echo ""

# 检查SSH连接
echo -e "${YELLOW}检查SSH连接...${NC}"
if ! ssh -o ConnectTimeout=5 $TARGET_USER@$TARGET_HOST "echo 'SSH连接成功'" > /dev/null 2>&1; then
    echo -e "${YELLOW}错误: 无法连接到目标机器${NC}"
    exit 1
fi
echo -e "${GREEN}✓ SSH连接正常${NC}"
echo ""

# 检查源路径
if [ ! -d "$SOURCE_PATH" ]; then
    echo -e "${YELLOW}错误: 源路径不存在: $SOURCE_PATH${NC}"
    exit 1
fi

# 显示同步前的统计
echo -e "${YELLOW}计算需要同步的文件...${NC}"
rsync -avn --stats \
    --exclude='*.tmp' \
    --exclude='*.lock' \
    --exclude='.git' \
    $SOURCE_PATH/ \
    $TARGET_USER@$TARGET_HOST:$TARGET_PATH/ 2>&1 | grep -E "(Number of files|Total file size)" || true
echo ""

# 询问是否继续
read -p "是否开始同步? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

# 开始同步
echo -e "${BLUE}=========================================="
echo "开始同步..."
echo "==========================================${NC}"
echo ""

# 使用rsync增量同步
rsync -avz --progress \
    --exclude='*.tmp' \
    --exclude='*.lock' \
    --exclude='.git' \
    --stats \
    --human-readable \
    $SOURCE_PATH/ \
    $TARGET_USER@$TARGET_HOST:$TARGET_PATH/ 2>&1 | tee $LOG_FILE

# 检查结果
if [ ${PIPESTATUS[0]} -eq 0 ]; then
    echo ""
    echo -e "${GREEN}=========================================="
    echo "✓ 同步完成！"
    echo "==========================================${NC}"
    echo -e "${GREEN}日志文件:${NC} $LOG_FILE"

    # 显示目标机器的磁盘使用情况
    echo ""
    echo -e "${YELLOW}目标机器磁盘使用情况:${NC}"
    ssh $TARGET_USER@$TARGET_HOST "df -h /data && echo '' && du -sh $TARGET_PATH"
else
    echo ""
    echo -e "${YELLOW}=========================================="
    echo "✗ 同步失败"
    echo "==========================================${NC}"
    echo -e "${YELLOW}请查看日志:${NC} $LOG_FILE"
    exit 1
fi
