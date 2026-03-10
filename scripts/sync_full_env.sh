#!/bin/bash
# 完整环境同步脚本 - 同步所有项目文件到5090机器
# 包括: wan22-service, ComfyUI, custom_nodes, claude配置等

set -e

# 配置
TARGET_HOST="192.168.16.7"
TARGET_USER="root"
LOG_FILE="/tmp/full_sync_$(date +%Y%m%d_%H%M%S).log"

# 颜色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# 同步项目列表
declare -A SYNC_ITEMS=(
    ["wan22-service"]="/home/gime/soft/wan22-service:/home/gime/soft/wan22-service"
    ["ComfyUI-code"]="/home/gime/soft/ComfyUI:/home/gime/soft/ComfyUI"
    ["ComfyUI-models"]="/home/gime/soft/ComfyUI/models:/data/ComfyUI/models"
    ["ComfyUI-custom-nodes"]="/home/gime/soft/ComfyUI/custom_nodes:/home/gime/soft/ComfyUI/custom_nodes"
    ["claude-binary"]="/root/.local/bin/claude:/root/.local/bin/claude"
    ["claude-config"]="/root/.claude:/root/.claude"
)

# 排除规则
EXCLUDE_RULES=(
    "--exclude=*.pyc"
    "--exclude=__pycache__"
    "--exclude=*.tmp"
    "--exclude=*.lock"
    "--exclude=.git"
    "--exclude=venv"
    "--exclude=node_modules"
    "--exclude=*.log"
    "--exclude=.env"
    "--exclude=storage/videos"
    "--exclude=storage/uploads"
    "--exclude=history.jsonl"
    "--exclude=file-history"
    "--exclude=session-env"
)

echo -e "${BLUE}=========================================="
echo "完整环境同步工具"
echo "目标: $TARGET_USER@$TARGET_HOST"
echo "==========================================${NC}"
echo ""

# 检查SSH连接
echo -e "${YELLOW}检查SSH连接...${NC}"
if ! ssh -o ConnectTimeout=5 $TARGET_USER@$TARGET_HOST "echo 'SSH连接成功'" > /dev/null 2>&1; then
    echo -e "${RED}错误: 无法连接到目标机器${NC}"
    exit 1
fi
echo -e "${GREEN}✓ SSH连接正常${NC}"
echo ""

# 显示同步项目
echo -e "${BLUE}将同步以下项目:${NC}"
for name in "${!SYNC_ITEMS[@]}"; do
    IFS=':' read -r src dst <<< "${SYNC_ITEMS[$name]}"
    if [ -e "$src" ]; then
        size=$(du -sh "$src" 2>/dev/null | cut -f1)
        echo -e "  ${GREEN}✓${NC} $name: $src ($size)"
    else
        echo -e "  ${YELLOW}⚠${NC} $name: $src (不存在，跳过)"
    fi
done
echo ""

# 询问是否继续
read -p "是否开始同步? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

echo "" | tee -a $LOG_FILE
echo -e "${BLUE}=========================================="
echo "开始同步..."
echo "==========================================${NC}"
echo "" | tee -a $LOG_FILE

# 同步每个项目
for name in "${!SYNC_ITEMS[@]}"; do
    IFS=':' read -r src dst <<< "${SYNC_ITEMS[$name]}"

    # 检查源路径是否存在
    if [ ! -e "$src" ]; then
        echo -e "${YELLOW}⚠ 跳过 $name: 源路径不存在${NC}" | tee -a $LOG_FILE
        continue
    fi

    echo "" | tee -a $LOG_FILE
    echo -e "${BLUE}>>> 同步: $name${NC}" | tee -a $LOG_FILE
    echo "    源: $src" | tee -a $LOG_FILE
    echo "    目标: $TARGET_USER@$TARGET_HOST:$dst" | tee -a $LOG_FILE
    echo "" | tee -a $LOG_FILE

    # 创建目标目录
    target_dir=$(dirname "$dst")
    ssh $TARGET_USER@$TARGET_HOST "mkdir -p $target_dir" 2>&1 | tee -a $LOG_FILE

    # 根据类型选择同步方式
    if [ -f "$src" ]; then
        # 单个文件
        rsync -avz --progress \
            "$src" \
            "$TARGET_USER@$TARGET_HOST:$dst" 2>&1 | tee -a $LOG_FILE
    else
        # 目录
        rsync -avz --progress \
            "${EXCLUDE_RULES[@]}" \
            "$src/" \
            "$TARGET_USER@$TARGET_HOST:$dst/" 2>&1 | tee -a $LOG_FILE
    fi

    if [ ${PIPESTATUS[0]} -eq 0 ]; then
        echo -e "${GREEN}✓ $name 同步完成${NC}" | tee -a $LOG_FILE
    else
        echo -e "${RED}✗ $name 同步失败${NC}" | tee -a $LOG_FILE
    fi
done

# 同步完成后的配置
echo "" | tee -a $LOG_FILE
echo -e "${BLUE}=========================================="
echo "配置目标机器..."
echo "==========================================${NC}"
echo "" | tee -a $LOG_FILE

ssh $TARGET_USER@$TARGET_HOST bash << 'REMOTE_SCRIPT' 2>&1 | tee -a $LOG_FILE
# 设置claude可执行权限
if [ -f /root/.local/bin/claude ]; then
    chmod +x /root/.local/bin/claude
    echo "✓ Claude二进制权限已设置"
fi

# 创建ComfyUI模型软链接（如果需要）
if [ -d /data/ComfyUI/models ] && [ ! -L /home/gime/soft/ComfyUI/models ]; then
    rm -rf /home/gime/soft/ComfyUI/models
    ln -sf /data/ComfyUI/models /home/gime/soft/ComfyUI/models
    echo "✓ ComfyUI模型目录软链接已创建"
fi

# 检查Python虚拟环境
if [ -d /home/gime/soft/wan22-service ] && [ ! -d /home/gime/soft/wan22-service/venv ]; then
    echo "⚠ wan22-service虚拟环境不存在，需要手动创建"
fi

if [ -d /home/gime/soft/ComfyUI ] && [ ! -d /home/gime/soft/ComfyUI/venv ]; then
    echo "⚠ ComfyUI虚拟环境不存在，需要手动创建"
fi

# 显示磁盘使用情况
echo ""
echo "磁盘使用情况:"
df -h / /data 2>/dev/null || df -h /

echo ""
echo "各项目大小:"
du -sh /home/gime/soft/wan22-service 2>/dev/null || echo "wan22-service: 不存在"
du -sh /home/gime/soft/ComfyUI 2>/dev/null || echo "ComfyUI: 不存在"
du -sh /data/ComfyUI/models 2>/dev/null || echo "models: 不存在"
du -sh /root/.claude 2>/dev/null || echo "claude config: 不存在"
REMOTE_SCRIPT

echo "" | tee -a $LOG_FILE
echo -e "${GREEN}=========================================="
echo "✓ 同步完成！"
echo "==========================================${NC}"
echo -e "${GREEN}日志文件:${NC} $LOG_FILE"
echo ""
echo -e "${YELLOW}后续步骤:${NC}"
echo "1. 在目标机器上创建Python虚拟环境"
echo "2. 安装依赖: pip install -r requirements.txt"
echo "3. 配置.env文件"
echo "4. 启动服务"
echo ""
