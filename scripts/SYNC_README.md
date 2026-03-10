# 5090机器同步脚本使用说明

## 脚本列表

### 1. 完整环境同步（推荐首次使用）
**路径**: `/home/gime/soft/wan22-service/scripts/sync_full_env.sh`

**功能**:
- 同步所有项目文件到5090机器
- 包括: wan22-service, ComfyUI代码, custom_nodes, claude配置
- 自动排除不必要的文件（日志、缓存、虚拟环境等）
- 同步后自动配置目标机器

**使用方法**:
```bash
bash /home/gime/soft/wan22-service/scripts/sync_full_env.sh
```

**同步内容**:
- wan22-service项目代码
- ComfyUI主程序代码
- ComfyUI自定义节点（custom_nodes）
- ComfyUI模型文件（到/data/ComfyUI/models）
- Claude二进制文件
- Claude配置文件

### 2. 快速代码同步（日常使用）
**路径**: `/home/gime/soft/wan22-service/scripts/sync_code_only.sh`

**功能**:
- 只同步代码变更，不包括模型
- 速度快，适合频繁更新
- 自动排除虚拟环境和缓存

**使用方法**:
```bash
bash /home/gime/soft/wan22-service/scripts/sync_code_only.sh
```

### 3. 模型增量同步
**路径**: `/home/gime/soft/wan22-service/scripts/sync_models_to_5090.sh`

**功能**:
- 增量同步，只传输新增或修改的文件
- 同步前显示需要传输的文件统计
- 询问确认后再开始传输
- 实时显示传输进度
- 保存详细日志

**使用方法**:
```bash
bash /home/gime/soft/wan22-service/scripts/sync_models_to_5090.sh
```

### 2. 自动同步脚本（无需确认）
**路径**: `/home/gime/soft/wan22-service/scripts/sync_models_auto.sh`

**功能**:
- 后台自动同步，无需人工确认
- 适合定时任务或自动化流程
- 记录日志到 `/tmp/model_sync_auto_*.log`

**使用方法**:
```bash
# 直接执行
bash /home/gime/soft/wan22-service/scripts/sync_models_auto.sh

# 后台执行
nohup bash /home/gime/soft/wan22-service/scripts/sync_models_auto.sh &
```

### 3. 设置定时自动同步（可选）

如果需要每天自动同步，可以添加到crontab：

```bash
# 编辑crontab
crontab -e

# 添加以下行（每天凌晨2点自动同步）
0 2 * * * /home/gime/soft/wan22-service/scripts/sync_models_auto.sh

# 或者每6小时同步一次
0 */6 * * * /home/gime/soft/wan22-service/scripts/sync_models_auto.sh
```

## 同步说明

### 增量同步原理
- rsync会自动比对源和目标文件
- 只传输新增、修改或删除的文件
- 已存在且未修改的文件会被跳过
- 大大节省传输时间

### 排除规则
以下文件会被自动排除：
- `*.tmp` - 临时文件
- `*.lock` - 锁文件
- `.git` - Git仓库文件

### 目标机器配置
- IP: 192.168.16.7
- 用户: root
- 路径: /data/ComfyUI/models
- 存储: 3.5TB NVMe SSD

## 示例场景

### 场景1: 下载了新的LoRA模型
```bash
# 1. 将新模型放到源目录
cp new_lora.safetensors /home/gime/soft/ComfyUI/models/loras/

# 2. 运行同步脚本
bash /home/gime/soft/wan22-service/scripts/sync_models_to_5090.sh

# 3. 脚本会自动检测并只传输新文件
```

### 场景2: 批量更新多个模型
```bash
# 直接运行同步脚本，会自动检测所有变化
bash /home/gime/soft/wan22-service/scripts/sync_models_to_5090.sh
```

### 场景3: 后台自动同步
```bash
# 适合在下载大量模型后，让它在后台慢慢同步
nohup bash /home/gime/soft/wan22-service/scripts/sync_models_auto.sh &

# 查看同步进度
tail -f /tmp/model_sync_auto_*.log
```

## 故障排查

### 问题1: SSH连接失败
```bash
# 测试SSH连接
ssh root@192.168.16.7 "echo test"

# 如果失败，重新添加SSH密钥
ssh-copy-id root@192.168.16.7
```

### 问题2: 磁盘空间不足
```bash
# 检查目标机器空间
ssh root@192.168.16.7 "df -h /data"

# 清理不需要的文件
ssh root@192.168.16.7 "du -sh /data/ComfyUI/models/*"
```

### 问题3: 查看同步日志
```bash
# 查看最新的同步日志
ls -lt /tmp/model_sync*.log | head -1 | xargs cat
```
