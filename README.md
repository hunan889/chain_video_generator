# Wan2.2 Video Service

基于 ComfyUI 和 Wan2.2 模型的视频生成服务，支持 T2V（文本生成视频）、I2V（图片生成视频）、Story 模式（多段连续视频）等功能。

## 功能特性

- ✅ **T2V 生成**：文本描述生成视频
- ✅ **I2V 生成**：图片生成视频
- ✅ **Story 模式**：多段连续视频生成，保持角色/场景一致性
- ✅ **LoRA 支持**：自动推荐和应用 LoRA 模型
- ✅ **Prompt 优化**：AI 自动优化提示词
- ✅ **多 GPU 支持**：支持多卡并行生成
- ✅ **视频扩展**：基于已有视频继续生成
- ✅ **自定义 Workflow**：支持上传和运行自定义 ComfyUI workflow
- ✅ **Web 界面**：简洁易用的 Web 管理界面
- ✅ **COS 存储**：支持腾讯云 COS 对象存储

## 系统要求

- **操作系统**：Ubuntu 20.04+ / Debian 11+
- **GPU**：NVIDIA GPU（推荐 RTX 4090 或更高）
- **VRAM**：24GB+（A14B 模型）
- **内存**：32GB+
- **存储**：100GB+（用于模型和视频存储）
- **Python**：3.11+
- **CUDA**：12.1+

## 快速开始

### 一键安装

```bash
# 克隆项目
git clone <your-repo-url>
cd wan22-service

# 运行一键安装脚本
sudo bash install.sh
```

安装脚本会自动完成：
1. 安装系统依赖（Python, Git, ffmpeg, Redis）
2. 克隆并配置 ComfyUI
3. 安装自定义节点（WanVideoWrapper, VideoHelperSuite 等）
4. 下载 Wan2.2 模型
5. 安装 API 服务依赖
6. 创建配置文件

### 配置

编辑 `.env` 文件配置您的参数：

```bash
nano .env
```

主要配置项：
- `COMFYUI_A14B_URL`: ComfyUI A14B 实例地址
- `A14B_GPU_IDS`: 分配给 A14B 的 GPU ID（逗号分隔）
- `API_PORT`: API 服务端口
- `COS_*`: 腾讯云 COS 配置（可选）

### 启动服务

```bash
# 启动所有服务（ComfyUI + API）
bash scripts/start_all.sh

# 查看服务状态
screen -ls

# 查看 ComfyUI 日志
screen -r comfyui_a14b

# 查看 API 日志
screen -r wan22_api

# 退出 screen（不停止服务）
Ctrl+A, 然后按 D
```

### 停止服务

```bash
bash scripts/stop_all.sh
```

### 访问服务

- **Web 界面**：http://localhost:8000
- **API 文档**：http://localhost:8000/docs
- **ComfyUI**：http://localhost:8188

## API 使用

### 文本生成视频（T2V）

```bash
curl -X POST http://localhost:8000/api/v1/generate/t2v \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A woman walking in a garden",
    "width": 832,
    "height": 480,
    "num_frames": 81,
    "fps": 24,
    "steps": 20
  }'
```

### 图片生成视频（I2V）

```bash
curl -X POST http://localhost:8000/api/v1/generate/i2v \
  -H "X-API-Key: your-api-key" \
  -F "image=@input.jpg" \
  -F 'params={
    "prompt": "A woman walking",
    "width": 832,
    "height": 480,
    "num_frames": 81,
    "fps": 24,
    "steps": 20
  }'
```

### Story 模式（多段连续视频）

```bash
curl -X POST http://localhost:8000/api/v1/generate/chain \
  -H "X-API-Key: your-api-key" \
  -F "image=@input.jpg" \
  -F 'params={
    "model": "a14b",
    "segments": [
      {"prompt": "A woman walking in a garden", "duration": 3.0},
      {"prompt": "She stops and smiles", "duration": 3.0}
    ],
    "width": 832,
    "height": 480,
    "fps": 16,
    "steps": 10,
    "story_mode": true
  }'
```

## 性能优化

### Story 模式性能对比

基于实际测试（832x480, 16fps, 10 steps, 2 segments）：

| 模式 | 总耗时 | 特点 |
|------|--------|------|
| **Story 模式（Merged）** | **3.3 分钟** | ✅ 视频连续性<br>✅ 身份一致性<br>✅ 模型共享<br>✅ 性能更快 |
| **标准 I2V（独立）** | **6.2 分钟** | ❌ 无连续性<br>❌ 每段重新加载模型<br>❌ 性能较慢 |

**结论**：对于多段视频生成，Story 模式比标准 I2V 快 **1.85x**，且提供更好的视频连续性。

### 优化建议

1. **降低 steps**：从 20 降到 10-15 可节省 25-50% 时间
2. **使用 Story 模式**：多段视频生成时性能更优
3. **合理分配 GPU**：避免 GPU 资源冲突
4. **启用 COS 存储**：减少本地存储压力

## 项目结构

```
wan22-service/
├── api/                    # API 服务代码
│   ├── routes/            # API 路由
│   ├── services/          # 业务逻辑
│   ├── models/            # 数据模型
│   └── static/            # 静态文件（Web 界面）
├── config/                # 配置文件
│   ├── api_keys.yaml     # API 密钥
│   └── loras.yaml        # LoRA 配置
├── scripts/               # 脚本工具
│   ├── start_all.sh      # 启动所有服务
│   ├── stop_all.sh       # 停止所有服务
│   ├── download_models.sh # 下载模型
│   └── download_loras.sh  # 下载 LoRA
├── workflows/             # ComfyUI workflow 文件
├── storage/               # 本地存储
│   ├── videos/           # 生成的视频
│   └── uploads/          # 上传的文件
├── install.sh            # 一键安装脚本
├── requirements.txt      # Python 依赖
└── .env                  # 环境变量配置
```

## 文档

- [API 文档](http://localhost:8000/docs)
- [Workflow 指南](WORKFLOW_GUIDE.md)
- [Story 模式说明](STORY_UPDATES.md)
- [时长计算指南](DURATION_GUIDE.md)

## 常见问题

### 1. ComfyUI 启动失败

检查 GPU 是否被占用：
```bash
nvidia-smi
```

查看 ComfyUI 日志：
```bash
screen -r comfyui_a14b
```

### 2. 模型下载失败

手动下载模型：
```bash
cd /home/gime/soft/wan22-service
bash scripts/download_models.sh
```

### 3. 生成速度慢

- 降低 steps（推荐 10-15）
- 使用 Story 模式（多段视频）
- 检查 GPU 利用率

### 4. 内存不足

- 减少并发任务数
- 降低分辨率
- 使用 fp8 量化模型

## 许可证

MIT License

## 致谢

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
- [Wan2.2 Model](https://huggingface.co/Kijai/wan2.2_comfyui)
- [ComfyUI-WanVideoWrapper](https://github.com/kijai/ComfyUI-WanVideoWrapper)
