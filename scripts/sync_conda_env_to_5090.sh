#!/bin/bash
# Conda环境同步脚本 - 同步conda环境到5090机器

set -e

TARGET_HOST="192.168.16.7"
TARGET_USER="root"
CONDA_ENV_DIR="/home/gime/soft/conda_env"
MINICONDA_DIR="/home/gime/soft/miniconda3"
LOG_FILE="/tmp/conda_sync_$(date +%Y%m%d_%H%M%S).log"

# 颜色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=========================================="
echo "Conda环境同步工具"
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

# 显示conda环境列表
echo -e "${BLUE}当前Conda环境:${NC}"
conda env list | grep -v "^#" | grep -v "^$"
echo ""

# 计算环境大小
echo -e "${YELLOW}计算环境大小...${NC}"
if [ -d "$CONDA_ENV_DIR" ]; then
    du -sh $CONDA_ENV_DIR/* 2>/dev/null | sort -h
fi
echo ""

read -p "是否同步所有conda环境? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

echo "" | tee -a $LOG_FILE
echo "==========================================" | tee -a $LOG_FILE
echo "开始同步 - $(date '+%Y-%m-%d %H:%M:%S')" | tee -a $LOG_FILE
echo "==========================================" | tee -a $LOG_FILE

# 1. 同步 miniconda3 基础环境
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[1/2] 同步 Miniconda 基础环境...${NC}" | tee -a $LOG_FILE
rsync -avz --progress \
    --exclude='pkgs/' \
    --exclude='envs/' \
    --exclude='*.pyc' \
    --exclude='__pycache__/' \
    $MINICONDA_DIR/ \
    $TARGET_USER@$TARGET_HOST:$MINICONDA_DIR/ 2>&1 | tee -a $LOG_FILE

# 2. 同步所有conda环境
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}[2/2] 同步 Conda 环境目录...${NC}" | tee -a $LOG_FILE

if [ -d "$CONDA_ENV_DIR" ]; then
    # 创建目标目录
    ssh $TARGET_USER@$TARGET_HOST "mkdir -p $CONDA_ENV_DIR"

    # 同步所有环境
    rsync -avz --progress \
        --exclude='*.pyc' \
        --exclude='__pycache__/' \
        --exclude='*.log' \
        --exclude='.git/' \
        $CONDA_ENV_DIR/ \
        $TARGET_USER@$TARGET_HOST:$CONDA_ENV_DIR/ 2>&1 | tee -a $LOG_FILE
else
    echo -e "${YELLOW}⚠ Conda环境目录不存在，跳过${NC}" | tee -a $LOG_FILE
fi

# 验证目标机器的conda环境
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}=========================================="
echo "目标机器Conda环境"
echo "==========================================${NC}"
ssh $TARGET_USER@$TARGET_HOST "
    export PATH=$MINICONDA_DIR/bin:\$PATH
    echo 'Conda版本:'
    conda --version 2>/dev/null || echo 'Conda未正确安装'
    echo ''
    echo 'Conda环境列表:'
    conda env list 2>/dev/null || echo '无法列出环境'
    echo ''
    echo '环境目录大小:'
    du -sh $CONDA_ENV_DIR/* 2>/dev/null || echo '环境目录为空'
" 2>&1 | tee -a $LOG_FILE

echo "" | tee -a $LOG_FILE
echo -e "${GREEN}=========================================="
echo "✓ Conda环境同步完成！"
echo "==========================================${NC}"
echo -e "${GREEN}日志文件:${NC} $LOG_FILE"
echo ""
echo -e "${YELLOW}后续步骤:${NC}"
echo "1. 在目标机器上添加conda到PATH:"
echo "   echo 'export PATH=/home/gime/soft/miniconda3/bin:\$PATH' >> ~/.bashrc"
echo "   source ~/.bashrc"
echo ""
echo "2. 验证环境:"
echo "   conda env list"
echo ""
echo "3. 激活环境测试:"
echo "   conda activate llm"
