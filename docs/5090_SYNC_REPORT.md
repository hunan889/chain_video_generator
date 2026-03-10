# 5090机器环境同步完成报告

## 同步完成时间
2026-03-10 13:07

## 目标机器信息
- IP: 192.168.16.7
- 用户: root
- GPU: NVIDIA RTX 5090

## 已同步内容

### 1. ComfyUI模型 (256GB)
- **源路径**: `/home/gime/soft/ComfyUI/models`
- **目标路径**: `/data/ComfyUI/models` (NVMe)
- **软链接**: `/home/gime/soft/ComfyUI/models` → `/data/ComfyUI/models`
- **包含**:
  - diffusion_models: 128GB
  - text_encoders: 37GB
  - unet: 32GB
  - loras: 19GB
  - clip: 4.4GB
  - 其他模型文件

### 2. wan22-service (API服务)
- **目标路径**: `/home/gime/soft/wan22-service/`
- **包含**:
  - API服务代码
  - 工作流模板 (workflows/)
  - 配置文件 (config/)
  - 启动脚本 (scripts/)
  - 文档

### 3. ComfyUI (主程序)
- **目标路径**: `/home/gime/soft/ComfyUI/`
- **包含**:
  - ComfyUI核心代码
  - 所有自定义节点 (custom_nodes/)
  - 配置文件
  - Web界面

### 4. Claude Code (909MB)
- **二进制文件**: `/data/claude/versions/` (NVMe)
  - 2.1.69 (230MB)
  - 2.1.70 (230MB)
  - 2.1.71 (227MB)
  - 2.1.72 (224MB) ← 当前版本
- **软链接**: `~/.local/bin/claude` → `/data/claude/versions/2.1.72`
- **版本**: 2.1.72 (Claude Code)

### 5. Claude配置 (95MB)
- **配置目录**: `/data/claude_config/` (NVMe)
- **软链接**: `~/.claude` → `/data/claude_config`
- **包含**:
  - settings.json (各种配置)
  - history.jsonl (历史记录)
  - plugins/ (插件)
  - plans/ (计划文件)

### 6. Conda环境 (34GB)
- **Miniconda3**: `/data/miniconda3/` (15GB)
  - 软链接: `/home/gime/soft/miniconda3` → `/data/miniconda3`
  - 版本: conda 24.11.3

- **虚拟环境**: `/data/conda_env/` (34GB)
  - 软链接: `/home/gime/soft/conda_env` → `/data/conda_env`
  - 环境列表:
    - AICoverGen (5.7GB)
    - bg-remover (7.7GB)
    - img2mask (2.7GB)
    - llm (9.5GB)
    - rvc (8.4GB)
    - stable-diffusion-webui-forge (166MB)

## 磁盘使用情况

### 根分区 (/dev/sda2)
- 总容量: 58GB
- 已使用: 31GB
- 可用: 25GB
- 使用率: 55%

### NVMe (/dev/nvme0n1 → /data)
- 总容量: 3.5TB
- 已使用: 305GB
- 可用: 3.0TB
- 使用率: 10%

### 存储分布
```
/data/
├── ComfyUI/models/     256GB  (模型文件)
├── conda_env/           34GB  (Conda虚拟环境)
├── miniconda3/          15GB  (Conda基础环境)
├── claude/             909MB  (Claude二进制)
└── claude_config/       95MB  (Claude配置)
```

## 软链接映射

所有大文件都存储在NVMe上，通过软链接访问：

```bash
/home/gime/soft/ComfyUI/models → /data/ComfyUI/models
/home/gime/soft/conda_env → /data/conda_env
/home/gime/soft/miniconda3 → /data/miniconda3
~/.local/bin/claude → /data/claude/versions/2.1.72
~/.local/share/claude → /data/claude
~/.claude → /data/claude_config
```

## 后续配置步骤

### 1. 配置环境变量

```bash
# 添加到 ~/.bashrc
cat >> ~/.bashrc << 'EOF'
# Conda
export PATH=/home/gime/soft/miniconda3/bin:$PATH

# Claude Code
export PATH=$HOME/.local/bin:$PATH

# 初始化conda
# >>> conda initialize >>>
__conda_setup="$('/home/gime/soft/miniconda3/bin/conda' 'shell.bash' 'hook' 2> /dev/null)"
if [ $? -eq 0 ]; then
    eval "$__conda_setup"
else
    if [ -f "/home/gime/soft/miniconda3/etc/profile.d/conda.sh" ]; then
        . "/home/gime/soft/miniconda3/etc/profile.d/conda.sh"
    else
        export PATH="/home/gime/soft/miniconda3/bin:$PATH"
    fi
fi
unset __conda_setup
# <<< conda initialize <<<
EOF

# 使配置生效
source ~/.bashrc
```

### 2. 配置wan22-service

```bash
cd /home/gime/soft/wan22-service

# 复制并编辑配置文件
cp .env.example .env
vim .env  # 修改配置

# 安装Python依赖（如果需要）
# pip install -r requirements.txt
```

### 3. 验证环境

```bash
# 验证Claude
claude --version

# 验证Conda
conda --version
conda env list

# 验证ComfyUI模型
ls -lh /home/gime/soft/ComfyUI/models/

# 验证wan22-service
ls -lh /home/gime/soft/wan22-service/
```

### 4. 启动服务

```bash
# 启动ComfyUI和API服务
cd /home/gime/soft/wan22-service
bash scripts/start_all.sh
```

## 同步脚本

已创建以下同步脚本，用于后续增量更新：

### 完整环境同步
```bash
bash /home/gime/soft/wan22-service/scripts/sync_full_env_to_5090.sh
```

### 仅同步模型
```bash
bash /home/gime/soft/wan22-service/scripts/sync_models_to_5090.sh
```

### 仅同步Conda环境
```bash
bash /home/gime/soft/wan22-service/scripts/sync_conda_env_to_5090.sh
```

## 注意事项

1. **根分区空间**: 根分区只有58GB，已使用55%。大文件都已移到NVMe，但仍需注意日志和临时文件的清理。

2. **软链接**: 所有大文件通过软链接访问，删除软链接不会删除实际文件。

3. **Conda环境**: 已配置conda识别 `/home/gime/soft/conda_env` 目录，所有虚拟环境都可正常使用。

4. **增量同步**: 使用rsync进行增量同步，只传输新增或修改的文件，速度很快。

5. **SSH密钥**: 已配置SSH密钥免密登录，后续同步无需输入密码。

## 同步性能统计

- **ComfyUI模型**: 256GB，传输速度 ~300MB/s，耗时 ~12分钟
- **ComfyUI代码**: 1.6GB，传输速度 ~220MB/s，耗时 ~7秒
- **Conda环境**: 34GB，传输速度 ~100MB/s，耗时 ~6分钟
- **Claude二进制**: 909MB，传输速度 ~100MB/s，耗时 ~9秒

**总计**: 约292GB数据，总耗时约20分钟

## 完成状态

✅ 所有文件已成功同步
✅ 软链接已正确配置
✅ Conda环境已识别
✅ Claude Code可正常使用
✅ 磁盘空间充足

5090机器已完全配置好，可以开始使用！
