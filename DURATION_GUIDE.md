# Story 视频生成 - 使用秒数指定时长

## 更新说明

现在可以直接使用**秒数**来指定视频时长，系统会自动转换为符合Wan2.2要求的帧数（4n+1格式）。

## 使用方法

### Web界面

1. 访问 http://localhost:8000
2. 点击"Story 视频"Tab
3. 选择workflow：`WAN2.2-I2V-AutoPrompt-Story`
4. 填写参数：
   - **时长(秒)**：3.3（或任意秒数）
   - **FPS**：24
   - **Prompt**：你的提示词
5. 点击"运行 Workflow"

系统会自动显示：`正在提交任务... (3.3秒 → 81帧)`

### API调用

现在支持两种方式：

#### 方式1：使用duration（推荐）

```bash
curl -X POST http://localhost:8000/api/v1/workflow/run \
  -H "Content-Type: application/json" \
  -H "X-API-Key: wan22-default-key-change-me" \
  -d '{
    "workflow_name": "WAN2.2-I2V-AutoPrompt-Story",
    "model": "a14b",
    "params": {
      "prompt": "A woman dancing",
      "duration": 3.3,
      "fps": 24,
      "width": 832,
      "height": 480,
      "steps": 20
    }
  }'
```

#### 方式2：直接使用num_frames（高级）

```bash
curl -X POST http://localhost:8000/api/v1/workflow/run \
  -H "Content-Type: application/json" \
  -H "X-API-Key: wan22-default-key-change-me" \
  -d '{
    "workflow_name": "WAN2.2-I2V-AutoPrompt-Story",
    "model": "a14b",
    "params": {
      "prompt": "A woman dancing",
      "num_frames": 81,
      "fps": 24,
      "width": 832,
      "height": 480
    }
  }'
```

## 时长转换对照表

| 输入时长 | FPS | 转换后帧数 | 实际时长 |
|---------|-----|-----------|---------|
| 3.3秒 | 24 | 81帧 | 3.38秒 |
| 4.0秒 | 24 | 97帧 | 4.04秒 |
| 5.0秒 | 24 | 121帧 | 5.04秒 |
| 6.0秒 | 24 | 145帧 | 6.04秒 |
| 10.0秒 | 24 | 241帧 | 10.04秒 |

**注意**：由于需要符合4n+1格式，实际时长可能会略有偏差（通常在0.1秒以内）。

## 转换算法

```python
def duration_to_frames(duration, fps):
    frames = max(round(duration * fps), 1)
    frames = round((frames - 1) / 4) * 4 + 1  # 对齐到4n+1
    return max(frames, 5)
```

例如：
- 3.3秒 × 24fps = 79.2 → 79帧 → 对齐到81帧（4×20+1）
- 4.0秒 × 24fps = 96帧 → 对齐到97帧（4×24+1）

## 多段Story视频

可以为每个场景指定不同的时长：

```bash
curl -X POST http://localhost:8000/api/v1/workflow/run \
  -H "Content-Type: application/json" \
  -H "X-API-Key: wan22-default-key-change-me" \
  -d '{
    "workflow_name": "WAN2.2-I2V-AutoPrompt-Story",
    "model": "a14b",
    "params": {
      "duration": 3.3,
      "fps": 24,
      "prompt": "Scene 1: Woman enters room (3.3s)",
      "prompt_2": "Scene 2: She walks to window",
      "prompt_3": "Scene 3: She looks outside",
      "prompt_4": "Scene 4: She smiles",
      "width": 832,
      "height": 480
    }
  }'
```

**注意**：CivitAI workflow的多个prompt会在同一个视频中生成，不是分段生成。如果需要分段生成并拼接，请使用"长视频生成"Tab。

## 常见问题

### Q: 为什么实际时长和输入时长不完全一致？

A: 因为Wan2.2模型要求帧数必须是4n+1格式（5, 9, 13, ..., 81, 85, ...），系统会自动对齐到最接近的有效帧数。偏差通常在0.1秒以内。

### Q: 可以生成多长的视频？

A: 理论上没有上限，但建议：
- 单段视频：3-6秒（81-145帧）
- 长视频：使用"长视频生成"功能，分多段生成后拼接

### Q: 如何精确控制帧数？

A: 如果需要精确的帧数，可以直接使用 `num_frames` 参数而不是 `duration`。

### Q: duration和num_frames同时提供会怎样？

A: `num_frames` 优先。如果同时提供，系统会使用 `num_frames` 并忽略 `duration`。

## 与"长视频生成"Tab的区别

| 功能 | Story 视频 Tab | 长视频生成 Tab |
|------|---------------|---------------|
| 用途 | 运行CivitAI workflow | 多段视频自动拼接 |
| 时长指定 | 单个时长 | 每段独立时长 |
| 生成方式 | 单次生成 | 分段生成+拼接 |
| 适用场景 | 测试workflow | 生成长视频 |

## 示例：生成不同时长的视频

```python
import requests

api_url = "http://localhost:8000/api/v1"
api_key = "wan22-default-key-change-me"

# 生成3.3秒视频
response = requests.post(
    f"{api_url}/workflow/run",
    headers={"X-API-Key": api_key},
    json={
        "workflow_name": "WAN2.2-I2V-AutoPrompt-Story",
        "model": "a14b",
        "params": {
            "prompt": "A woman dancing gracefully",
            "duration": 3.3,  # 自动转换为81帧
            "fps": 24,
            "width": 832,
            "height": 480,
            "steps": 20
        }
    }
)

print(f"Task ID: {response.json()['task_id']}")
```

## 总结

✅ **现在更简单**：直接输入秒数，无需计算帧数
✅ **自动对齐**：系统自动转换为4n+1格式
✅ **实时反馈**：提交时显示转换结果（如"3.3秒 → 81帧"）
✅ **向后兼容**：仍然支持直接使用num_frames参数
