#!/bin/bash
# 快速同步脚本 - 只同步代码变更，不包括模型

TARGET_HOST="192.168.16.7"
TARGET_USER="root"

echo "快速同步代码到5090..."

# 同步wan22-service
echo ">>> 同步 wan22-service..."
rsync -avz --progress \
    --exclude='*.pyc' --exclude='__pycache__' --exclude='venv' \
    --exclude='storage/videos' --exclude='storage/uploads' --exclude='*.log' \
    /home/gime/soft/wan22-service/ \
    $TARGET_USER@$TARGET_HOST:/home/gime/soft/wan22-service/

# 同步ComfyUI代码（不包括models）
echo ">>> 同步 ComfyUI代码..."
rsync -avz --progress \
    --exclude='*.pyc' --exclude='__pycache__' --exclude='venv' \
    --exclude='models' --exclude='output' --exclude='input' --exclude='temp' \
    /home/gime/soft/ComfyUI/ \
    $TARGET_USER@$TARGET_HOST:/home/gime/soft/ComfyUI/

# 同步custom_nodes
echo ">>> 同步 custom_nodes..."
rsync -avz --progress \
    --exclude='*.pyc' --exclude='__pycache__' \
    /home/gime/soft/ComfyUI/custom_nodes/ \
    $TARGET_USER@$TARGET_HOST:/home/gime/soft/ComfyUI/custom_nodes/

echo "✓ 代码同步完成！"
