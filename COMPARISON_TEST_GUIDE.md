# 视频生成性能对比测试指南

## 快速开始

### 1. 准备测试图片

将一张测试图片放到 `storage/uploads/` 目录：

```bash
# 示例：从其他位置复制图片
cp /path/to/your/test_image.jpg storage/uploads/

# 或者使用现有的上传图片
ls storage/uploads/
```

### 2. 确保服务运行

```bash
# 检查 ComfyUI 是否运行
screen -ls | grep comfyui

# 如果没有运行，启动服务
bash scripts/start_all.sh
```

### 3. 运行对比测试

```bash
cd /home/gime/soft/wan22-service
python3 run_comparison_test.py
```

## 测试内容

脚本会自动执行以下测试：

### 测试 1: Story 模式（Merged）
- 生成 2 段连续视频
- 使用共享模型（只加载一次）
- 保持视频连续性和身份一致性

### 测试 2: 标准 I2V（独立）
- 生成 2 段独立视频
- 每段重新加载模型
- 最后合并成完整视频
- 无连续性保证

## 测试配置

```
分辨率: 832x480
FPS: 16
Steps: 15
段数: 2段（每段 49帧 ≈ 3秒）
模型: A14B
```

## 输出结果

测试完成后会生成：

1. **视频文件**
   - Story 模式: `storage/videos/[hash].mp4`（合并后的完整视频）
   - 标准 I2V: `storage/videos/[hash].mp4`（合并后的完整视频）

2. **性能数据** (`comparison_results.json`)
   ```json
   {
     "story_mode": {
       "elapsed_seconds": 198.5,
       "video_url": "...",
       "status": "completed"
     },
     "standard_i2v": {
       "elapsed_seconds": 372.1,
       "video_urls": ["...", "..."],
       "status": "completed"
     }
   }
   ```

3. **对比报告** (`comparison_report.txt`)
   ```
   1. Story 模式（Merged）:
      耗时: 3.31 分钟 (198.5 秒)
      视频: /api/v1/results/xxx.mp4

   2. 标准 I2V（独立生成 + 合并）:
      耗时: 6.20 分钟 (372.1 秒)
      合并视频: /api/v1/results/xxx.mp4

   性能对比:
     Story 模式快 1.87x
     节省时间: 2.89 分钟
   ```

## 查看结果

### 方法 1: 通过 Web 界面

访问 http://localhost:8000 查看生成的视频

### 方法 2: 直接访问文件

```bash
# 查看最新生成的视频
ls -lht storage/videos/ | head -5

# 使用视频播放器查看
vlc storage/videos/[filename].mp4
```

### 方法 3: 通过 API

```bash
# 查看 Story 模式视频
curl http://localhost:8000/api/v1/results/[filename].mp4 -o story_mode.mp4

# 查看标准 I2V 视频
curl http://localhost:8000/api/v1/results/[filename].mp4 -o i2v_merged.mp4
```

## 预期结果

基于之前的测试数据，预期结果：

- **Story 模式**: 4-5 分钟（15 steps）
- **标准 I2V**: 8-10 分钟（15 steps）
- **性能提升**: 1.8-2.0x

## 故障排除

### 问题 1: 找不到测试图片

```bash
# 检查 uploads 目录
ls storage/uploads/

# 如果为空，上传一张图片
cp /path/to/image.jpg storage/uploads/
```

### 问题 2: ComfyUI 未运行

```bash
# 检查服务状态
screen -ls

# 启动 ComfyUI
bash scripts/start_all.sh

# 查看日志
screen -r comfyui_a14b
```

### 问题 3: GPU 内存不足

```bash
# 检查 GPU 使用情况
nvidia-smi

# 如果 GPU 被占用，等待其他任务完成
# 或者降低测试参数（减少 num_frames 或 steps）
```

## 自定义测试

如果想修改测试参数，编辑 `run_comparison_test.py`：

```python
# 修改步数
"steps": 15,  # 从 10 改为 15（更高质量）

# 修改帧数
"num_frames": 33,  # 从 49 改为 33（约 2 秒）

# 修改步数
"steps": 8,  # 从 15 改为 8（更快但质量略降）
```

## 注意事项

1. **测试时间**: 完整测试需要 15-20 分钟
2. **GPU 占用**: 测试期间 GPU 会满载运行
3. **存储空间**: 确保有足够空间存储视频（每段约 1-2MB）
4. **并发限制**: 测试期间避免运行其他生成任务
5. **公平对比**: 两种模式都会生成合并后的完整视频

## 分析工具

测试完成后，可以使用分析工具查看详细数据：

```bash
# 分析 ComfyUI 日志
python3 analyze_performance.py

# 查看对比报告
cat comparison_report.txt

# 查看 JSON 数据
cat comparison_results.json | python3 -m json.tool
```
