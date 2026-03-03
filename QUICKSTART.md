# 快速开始指南

## 一键安装

```bash
# 1. 克隆项目
git clone <your-repo-url>
cd wan22-service

# 2. 运行一键安装脚本（需要 root 权限）
sudo bash install.sh
```

安装脚本会自动完成以下操作：
- ✅ 安装系统依赖（Python 3.11, Git, ffmpeg, Redis）
- ✅ 克隆 ComfyUI 到父目录
- ✅ 创建 Python 虚拟环境
- ✅ 安装 PyTorch 和 ComfyUI 依赖
- ✅ 安装自定义节点（WanVideoWrapper, VideoHelperSuite, rgthree-comfy 等）
- ✅ 下载 Wan2.2 模型（A14B HIGH/LOW, VAE, CLIP）
- ✅ 安装 API 服务依赖
- ✅ 创建配置文件和必要目录

## 配置

编辑 `.env` 文件：

```bash
nano .env
```

关键配置项：

```bash
# ComfyUI 配置
COMFYUI_A14B_URL=http://127.0.0.1:8188
A14B_GPU_IDS=0,1,2  # 分配给 A14B 的 GPU

# API 配置
API_PORT=8000
API_HOST=0.0.0.0

# 存储配置（可选）
COS_BUCKET=your-bucket  # 留空使用本地存储
COS_REGION=na-ashburn
COS_SECRET_ID=your-id
COS_SECRET_KEY=your-key
```

## 启动服务

```bash
# 启动所有服务
bash scripts/start_all.sh
```

这会启动：
- ComfyUI A14B 实例（端口 8188）
- API 服务（端口 8000）

查看服务状态：
```bash
screen -ls
```

查看日志：
```bash
# ComfyUI 日志
screen -r comfyui_a14b

# API 日志
screen -r wan22_api

# 退出 screen（不停止服务）
Ctrl+A, 然后按 D
```

## 访问服务

- **Web 界面**：http://localhost:8000
- **API 文档**：http://localhost:8000/docs
- **ComfyUI**：http://localhost:8188

## 快速测试

### 1. 文本生成视频（T2V）

```bash
curl -X POST http://localhost:8000/api/v1/generate/t2v \
  -H "X-API-Key: wan22-default-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A beautiful sunset over the ocean",
    "width": 832,
    "height": 480,
    "num_frames": 81,
    "fps": 24,
    "steps": 15
  }'
```

### 2. 图片生成视频（I2V）

```bash
curl -X POST http://localhost:8000/api/v1/generate/i2v \
  -H "X-API-Key: wan22-default-key-change-me" \
  -F "image=@/path/to/your/image.jpg" \
  -F 'params={
    "prompt": "A woman walking forward",
    "width": 832,
    "height": 480,
    "num_frames": 81,
    "fps": 24,
    "steps": 15
  }'
```

### 3. Story 模式（多段连续视频）

```bash
curl -X POST http://localhost:8000/api/v1/generate/chain \
  -H "X-API-Key: wan22-default-key-change-me" \
  -F "image=@/path/to/your/image.jpg" \
  -F 'params={
    "model": "a14b",
    "segments": [
      {"prompt": "A woman walking in a garden", "duration": 3.0},
      {"prompt": "She stops and looks at flowers", "duration": 3.0},
      {"prompt": "She smiles at the camera", "duration": 3.0}
    ],
    "width": 832,
    "height": 480,
    "fps": 16,
    "steps": 10,
    "story_mode": true
  }'
```

### 4. 查询任务状态

```bash
# 获取任务 ID 后查询
curl http://localhost:8000/api/v1/tasks/{task_id} \
  -H "X-API-Key: wan22-default-key-change-me"
```

## 停止服务

```bash
bash scripts/stop_all.sh
```

## 常见问题

### Q: 安装失败怎么办？

A: 检查以下几点：
1. 是否使用 root 权限运行
2. 网络连接是否正常（需要下载模型）
3. 磁盘空间是否充足（至少 100GB）
4. GPU 驱动是否正确安装

### Q: ComfyUI 启动失败？

A: 
```bash
# 检查 GPU 状态
nvidia-smi

# 查看 ComfyUI 日志
screen -r comfyui_a14b

# 手动启动 ComfyUI
cd /home/gime/soft/ComfyUI
source venv/bin/activate
CUDA_VISIBLE_DEVICES=0,1,2 python main.py --port 8188
```

### Q: 模型下载失败？

A:
```bash
# 手动下载模型
cd /home/gime/soft/wan22-service
bash scripts/download_models.sh

# 或者从 HuggingFace 手动下载
# https://huggingface.co/Kijai/wan2.2_comfyui
```

### Q: 生成速度慢？

A: 优化建议：
1. 降低 steps（推荐 10-15）
2. 使用 Story 模式（多段视频时更快）
3. 检查 GPU 利用率：`nvidia-smi`
4. 确保没有其他进程占用 GPU

### Q: 如何使用自己的 LoRA？

A:
1. 将 LoRA 文件放到 `ComfyUI/models/loras/`
2. 编辑 `config/loras.yaml` 添加配置
3. 在 API 请求中指定 LoRA

## 性能参考

基于 RTX 4090 测试（832x480, 16fps）：

| 模式 | Steps | 时长 | 生成时间 |
|------|-------|------|----------|
| T2V | 15 | 3.3秒 | ~2分钟 |
| I2V | 15 | 3.3秒 | ~2分钟 |
| Story (2段) | 10 | 5秒 | ~3.3分钟 |
| Story (4段) | 10 | 10秒 | ~6-7分钟 |

**提示**：Story 模式比独立生成多段视频快 1.85x！

## 下一步

- 阅读 [完整文档](README.md)
- 查看 [API 文档](http://localhost:8000/docs)
- 了解 [Story 模式](STORY_UPDATES.md)
- 学习 [Workflow 自定义](WORKFLOW_GUIDE.md)

## 获取帮助

如果遇到问题：
1. 查看日志：`screen -r comfyui_a14b` 或 `screen -r wan22_api`
2. 检查 GPU：`nvidia-smi`
3. 查看文档：`README.md`
4. 提交 Issue

祝您使用愉快！🎉
