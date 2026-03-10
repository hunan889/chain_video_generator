#!/bin/bash
# 自动同步脚本 - 后台运行，无需确认

SOURCE_PATH="/home/gime/soft/ComfyUI/models"
TARGET_HOST="192.168.16.7"
TARGET_USER="root"
TARGET_PATH="/data/ComfyUI/models"
LOG_FILE="/tmp/model_sync_auto_$(date +%Y%m%d_%H%M%S).log"

echo "$(date '+%Y-%m-%d %H:%M:%S') - 开始自动同步模型..." | tee -a $LOG_FILE

rsync -avz \
    --exclude='*.tmp' \
    --exclude='*.lock' \
    --exclude='.git' \
    --stats \
    $SOURCE_PATH/ \
    $TARGET_USER@$TARGET_HOST:$TARGET_PATH/ >> $LOG_FILE 2>&1

if [ $? -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 同步完成" | tee -a $LOG_FILE
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 同步失败" | tee -a $LOG_FILE
fi
