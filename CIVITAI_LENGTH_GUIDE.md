# CivitAI Workflow 长度设置说明

## 问题：Workflow会自动计算长度吗？

**答案：不会，需要手动指定。**

## CivitAI Story Workflow 的参数

### 1. 主要参数

CivitAI的 `WAN2.2-I2V-AutoPrompt-Story.json` workflow使用以下参数：

| 参数名 | 节点标题 | 默认值 | 说明 |
|--------|----------|--------|------|
| `num_frames` | Lenght | 81 | 视频帧数（注意拼写是Lenght） |
| `steps` | Steps | 5 | 采样步数 |
| `width` | WIDTH | 832 | 视频宽度 |
| `height` | HEIGHT | 480 | 视频高度 |
| `prompt` | prompt_1 | - | 第一段提示词 |
| `prompt_2` | prompt_2 | - | 第二段提示词 |
| `prompt_3` | prompt_3 | - | 第三段提示词 |
| `prompt_4` | prompt_4 | - | 第四段提示词 |

### 2. 帧数要求

**重要：帧数必须符合 4n+1 格式**

有效的帧数：5, 9, 13, 17, 21, 25, ..., 77, 81, 85, 89, ...

常用帧数对应时长（@24fps）：
- 81帧 = 3.375秒
- 97帧 = 4.04秒
- 121帧 = 5.04秒
- 145帧 = 6.04秒

## 使用方法

### 方式1：Web界面（推荐）

1. 访问 http://localhost:8000
2. 点击"Story 视频"Tab
3. 选择workflow：`WAN2.2-I2V-AutoPrompt-Story`
4. 填写参数：
   - **帧数 (Length)**：81（或其他4n+1值）
   - **Prompt**：你的提示词
   - **宽度/高度**：832x480
   - **Steps**：20
5. 点击"运行 Workflow"

### 方式2：API调用

```bash
curl -X POST http://localhost:8000/api/v1/workflow/run \
  -H "Content-Type: application/json" \
  -H "X-API-Key: wan22-default-key-change-me" \
  -d '{
    "workflow_name": "WAN2.2-I2V-AutoPrompt-Story",
    "model": "a14b",
    "params": {
      "prompt": "A beautiful woman walking in the park",
      "num_frames": 81,
      "width": 832,
      "height": 480,
      "steps": 20,
      "cfg": 6.0,
      "shift": 5.0,
      "seed": -1
    }
  }'
```

### 方式3：多段Story视频

如果要生成多段视频（Story模式），可以使用多个prompt参数：

```bash
curl -X POST http://localhost:8000/api/v1/workflow/run \
  -H "Content-Type: application/json" \
  -H "X-API-Key: wan22-default-key-change-me" \
  -d '{
    "workflow_name": "WAN2.2-I2V-AutoPrompt-Story",
    "model": "a14b",
    "params": {
      "prompt": "Scene 1: A woman enters a room",
      "prompt_2": "Scene 2: She walks to the window",
      "prompt_3": "Scene 3: She looks outside",
      "prompt_4": "Scene 4: She smiles",
      "num_frames": 81,
      "width": 832,
      "height": 480,
      "steps": 20
    }
  }'
```

## 参数替换机制

我们的API支持两种参数替换方式：

### 1. 占位符替换（简单workflow）

在workflow JSON中使用 `${参数名}`：

```json
{
  "inputs": {
    "text": "${prompt}",
    "width": ${width}
  }
}
```

### 2. 节点标题匹配（CivitAI workflow）

API会自动找到对应的节点并更新值：

- `num_frames` → 更新 `mxSlider` 节点（Title: "Lenght"）
- `steps` → 更新 `mxSlider` 节点（Title: "Steps"）
- `prompt` → 更新提示词节点

## 常见问题

### Q1: 为什么是"Lenght"而不是"Length"？

A: 这是CivitAI workflow中的拼写，我们的API会自动映射 `num_frames`/`length`/`lenght` 到这个节点。

### Q2: 如何生成更长的视频？

A: 有两种方式：

**方式1：增加单个视频的帧数**
```json
{
  "num_frames": 145  // 约6秒 @24fps
}
```

**方式2：使用多段拼接（推荐）**
使用"长视频生成"Tab，创建多个分段，每段3-4秒，系统会自动拼接。

### Q3: 帧数不是4n+1会怎样？

A: ComfyUI会报错。建议使用这些值：
- 短视频：81帧（3.4秒）
- 中等：97帧（4秒）或 121帧（5秒）
- 长视频：145帧（6秒）或 169帧（7秒）

### Q4: CivitAI workflow支持哪些高级功能？

A: 该workflow包含：
- 自动prompt优化
- 多段story生成（prompt_1到prompt_4）
- 运动控制（motion_amplitude）
- 颜色匹配
- 可选的upscale

可以通过自定义参数传入：

```json
{
  "params": {
    "num_frames": 81,
    "prompt": "Main scene",
    "prompt_2": "Second scene",
    "motion_amplitude": 0.5,
    "color_match": true
  }
}
```

## 总结

1. **长度需要手动指定**，通过 `num_frames` 参数
2. **必须是4n+1格式**（81, 97, 121, 145...）
3. **推荐使用81帧**作为起点（约3.4秒）
4. **多段视频**使用 prompt_1/2/3/4 或使用"长视频生成"功能
5. **API会自动处理**参数映射，无需修改workflow JSON

## 下一步

- 尝试运行workflow并调整参数
- 使用多个prompt创建story视频
- 结合"长视频生成"功能创建更长的视频
