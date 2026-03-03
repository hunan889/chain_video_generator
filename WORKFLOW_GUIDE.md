# CivitAI Workflow 集成指南

## 概述

现在你可以直接在系统中运行任何ComfyUI workflow文件，包括从CivitAI下载的复杂workflow。

## 功能特点

1. **直接运行ComfyUI workflow** - 无需修改代码，直接加载JSON文件
2. **参数替换** - 在workflow中使用占位符，通过API动态替换
3. **Web界面** - 新增"Story 视频"Tab，提供友好的UI
4. **API支持** - 完整的REST API，支持自动化调用

## 使用方法

### 1. 准备Workflow文件

将你的ComfyUI workflow JSON文件放到 `/home/gime/soft/wan22-service/workflows/` 目录。

例如：
- `WAN2.2-I2V-AutoPrompt-Story.json` (已下载)
- 你自己的workflow文件

### 2. 在Workflow中使用参数占位符

在workflow JSON中，使用 `${参数名}` 作为占位符：

```json
{
  "nodes": [
    {
      "type": "WanVideoTextEncode",
      "inputs": {
        "text": "${prompt}"
      }
    },
    {
      "type": "WanVideoSampler",
      "inputs": {
        "width": ${width},
        "height": ${height},
        "num_frames": ${num_frames},
        "steps": ${steps},
        "cfg": ${cfg},
        "seed": ${seed}
      }
    }
  ]
}
```

### 3. 通过Web界面使用

1. 访问 http://localhost:8000
2. 点击"Story 视频"Tab
3. 从下拉菜单选择workflow
4. 填写参数（prompt, width, height等）
5. 点击"运行 Workflow"

### 4. 通过API使用

#### 列出所有可用的workflow

```bash
curl http://localhost:8000/api/v1/workflow/list \
  -H "X-API-Key: your-api-key"
```

响应：
```json
{
  "workflows": [
    {
      "name": "WAN2.2-I2V-AutoPrompt-Story",
      "filename": "WAN2.2-I2V-AutoPrompt-Story.json",
      "size": 342622
    }
  ]
}
```

#### 运行workflow

```bash
curl -X POST http://localhost:8000/api/v1/workflow/run \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "workflow_name": "WAN2.2-I2V-AutoPrompt-Story",
    "model": "a14b",
    "params": {
      "prompt": "A beautiful woman walking in the park",
      "width": 832,
      "height": 480,
      "num_frames": 81,
      "fps": 24,
      "steps": 20,
      "cfg": 6.0,
      "shift": 5.0,
      "seed": -1
    }
  }'
```

响应：
```json
{
  "task_id": "abc123",
  "status": "queued"
}
```

#### 查询任务状态

```bash
curl http://localhost:8000/api/v1/tasks/abc123 \
  -H "X-API-Key: your-api-key"
```

## 参数说明

### 基本参数

- `workflow_name`: workflow文件名（不含.json扩展名）
- `model`: 使用的模型 (`a14b` 或 `5b`)
- `params`: 参数字典，会替换workflow中的占位符

### 常用参数

- `prompt`: 提示词
- `width`: 视频宽度（建议832）
- `height`: 视频高度（建议480）
- `num_frames`: 帧数（81帧约3.3秒@24fps）
- `fps`: 帧率（建议24）
- `steps`: 采样步数（建议20）
- `cfg`: CFG scale（建议6.0）
- `shift`: Shift值（建议5.0）
- `seed`: 随机种子（-1为随机）

### 自定义参数

你可以在`params`中添加任何自定义参数，只要在workflow中有对应的占位符：

```json
{
  "params": {
    "prompt": "...",
    "custom_param1": "value1",
    "custom_param2": 123,
    "lora_strength": 0.8
  }
}
```

## CivitAI Workflow 特性

### 角色一致性优化

CivitAI的Story workflow包含以下特性来改善角色一致性：

1. **低噪声增强** - 使用较低的noise_aug_strength (0.01-0.05)
2. **帧提取策略** - 支持提取中间帧而非最后一帧
3. **颜色校正** - 保持视频段之间的颜色一致性
4. **Prompt连续性** - 自动优化prompt以保持角色特征

### 推荐设置

对于多段视频生成，建议：

```json
{
  "noise_aug_strength": 0.02,
  "frame_position": "middle",
  "color_match": true,
  "color_match_method": "mkl"
}
```

## 故障排除

### Workflow加载失败

- 检查JSON格式是否正确
- 确保文件在workflows目录中
- 查看API日志：`tail -f /tmp/wan22-api.log`

### 参数替换不生效

- 确保占位符格式正确：`${param_name}`
- 参数名区分大小写
- 数值类型不需要引号

### ComfyUI节点缺失

CivitAI workflow可能使用了自定义节点，需要安装：

```bash
cd /home/gime/soft/ComfyUI/custom_nodes
git clone <node-repo-url>
```

常用节点：
- rgthree-comfy
- ComfyUI-VideoHelperSuite
- ComfyUI_UltimateSDUpscale

## 示例：运行Story Workflow

```python
import requests

api_url = "http://localhost:8000/api/v1"
api_key = "your-api-key"

# 运行workflow
response = requests.post(
    f"{api_url}/workflow/run",
    headers={"X-API-Key": api_key},
    json={
        "workflow_name": "WAN2.2-I2V-AutoPrompt-Story",
        "model": "a14b",
        "params": {
            "prompt": "A woman dancing gracefully",
            "width": 832,
            "height": 480,
            "num_frames": 81,
            "fps": 24,
            "steps": 20,
            "cfg": 6.0,
            "shift": 5.0,
            "seed": 42
        }
    }
)

task_id = response.json()["task_id"]
print(f"Task created: {task_id}")

# 查询结果
import time
while True:
    status = requests.get(
        f"{api_url}/tasks/{task_id}",
        headers={"X-API-Key": api_key}
    ).json()

    if status["status"] == "completed":
        print(f"Video URL: {status['video_url']}")
        break
    elif status["status"] == "failed":
        print(f"Failed: {status.get('error')}")
        break

    time.sleep(5)
```

## 下一步

1. 从CivitAI下载更多workflow
2. 根据需要修改workflow参数
3. 创建自己的workflow模板
4. 集成到自动化流程中
