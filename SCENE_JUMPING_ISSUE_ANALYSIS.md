# 视频场景跳跃问题分析报告

## 任务信息
- **任务ID**: 3ddaf1341b7f4ad49ca7f92eee1c0728
- **问题**: 生成的视频前后场景完全无关联，直接跳场景
- **状态**: 已完成，但结果不符合预期

---

## 根本原因

### ❌ Prompt 使用了错误的时间标记语法

**用户的 Prompt**:
```
(at 0 seconds: close-up shot of a girl in a dimly lit room, slowly taking off her top, her hands moving with confidence, her face showing a slight smile) (at 2 seconds: she removes her pants, now completely naked, standing before him, her body relaxed, eyes looking directly at the camera)
```

**问题**:
1. **Wan2.2 模型不支持 `(at X seconds: ...)` 时间标记语法**
2. 模型会将整个 prompt 同时应用到所有帧
3. Prompt 包含两个矛盾的场景描述：
   - 场景1: 脱上衣 + 微笑
   - 场景2: 脱裤子 + 全裸 + 看镜头
4. 模型试图同时满足两个冲突的描述，导致视频中间突然跳转

### 为什么会跳场景？

```
视频时长: 4.06秒 (65帧 / 16fps)

模型的理解:
- 前半段: 随机选择场景1或场景2的某些元素
- 后半段: 随机选择另一个场景的元素
- 结果: 场景不连贯，出现跳跃

用户期望:
- 0-2秒: 场景1
- 2-4秒: 场景2

实际情况:
- 模型无法理解时间标记
- 随机混合两个场景的描述
- 导致视频不连贯
```

---

## 解决方案

### 方案1: 使用单一连贯的 Prompt ⭐ 推荐

**原则**: 描述一个连贯的动作序列，避免多个独立场景

**错误示例** (会跳场景):
```
(at 0 seconds: girl standing) (at 2 seconds: girl sitting)
```

**正确示例** (连贯动作):
```
close-up shot of a girl in a dimly lit room, slowly undressing with smooth continuous motion, maintaining confident eye contact with the camera throughout
```

**优点**:
- ✅ 单个 prompt，简单直接
- ✅ 描述连贯动作，避免场景跳跃
- ✅ 适合短视频 (< 5秒)

**缺点**:
- ❌ 无法精确控制不同时间段的动作
- ❌ 复杂场景变化难以描述

---

### 方案2: 使用长视频生成 (Chain Mode) ⭐⭐ 推荐

**原理**: 将视频分成多个 segment，每个 segment 使用独立的 prompt

**实现**:
```json
{
  "segments": [
    {
      "prompt": "close-up shot of a girl in a dimly lit room, slowly taking off her top, her hands moving with confidence, her face showing a slight smile",
      "duration": 2.0
    },
    {
      "prompt": "the girl removing her pants, now undressed, her body relaxed, eyes looking directly at the camera",
      "duration": 2.0
    }
  ],
  "model": "a14b",
  "model_preset": "nsfw_v2",
  "width": 416,
  "height": 736,
  "fps": 16,
  "auto_continue": true
}
```

**优点**:
- ✅ 每个 segment 有独立的 prompt
- ✅ 可以精确控制不同时间段的内容
- ✅ `auto_continue: true` 会使用 VLM 自动生成连贯的过渡
- ✅ 适合中长视频 (5-20秒)

**缺点**:
- ⚠️ 不同 segment 之间可能出现身份不一致（角色外观变化）

---

### 方案3: 使用 Story Mode (最佳) ⭐⭐⭐ 强烈推荐

**原理**: 使用 Story 模式保持身份一致性，同时支持多段 prompt

**实现**:
```json
{
  "segments": [
    {
      "prompt": "close-up shot of a girl in a dimly lit room, slowly taking off her top, her hands moving with confidence, her face showing a slight smile",
      "duration": 2.0
    },
    {
      "prompt": "the same girl removing her pants, now undressed, her body relaxed, eyes looking directly at the camera",
      "duration": 2.0
    }
  ],
  "model": "a14b",
  "model_preset": "nsfw_v2",
  "width": 416,
  "height": 736,
  "fps": 16,
  "story_mode": true,
  "motion_frames": 5,
  "boundary": 0.9,
  "clip_preset": "nsfw",
  "auto_continue": true
}
```

**优点**:
- ✅ 每个 segment 有独立的 prompt
- ✅ 保持角色身份一致（使用 initial_reference_image）
- ✅ 场景过渡自然连贯
- ✅ 适合需要保持角色一致性的长视频

**缺点**:
- ⚠️ 需要上传首帧图片作为身份参考
- ⚠️ 生成时间略长（约 +12秒）

---

## 技术细节

### Wan2.2 模型的 Prompt 处理机制

```python
# 模型如何处理 prompt
def process_prompt(prompt, num_frames):
    # 1. 将 prompt 编码为 text embeddings
    text_embeds = t5_encoder.encode(prompt)

    # 2. 将 text_embeds 应用到所有帧
    for frame in range(num_frames):
        latents[frame] = apply_text_guidance(latents[frame], text_embeds)

    # 注意: 模型不会解析时间标记，所有帧使用相同的 text_embeds
```

### 为什么时间标记不起作用？

1. **T5 文本编码器**将整个 prompt 编码为单个向量
2. **扩散模型**将这个向量应用到所有帧
3. **没有时间条件机制**来区分不同时间段的描述

### Chain Mode 的工作原理

```
Segment 1:
  Input: 首帧图片
  Prompt: "girl taking off her top"
  Output: 视频1 (0-2秒)

Segment 2:
  Input: 视频1的最后一帧
  Prompt: "girl removing her pants"
  Output: 视频2 (2-4秒)

最终: 拼接视频1 + 视频2
```

### Story Mode 的工作原理

```
Segment 1:
  Node: PainterI2V
  Input: 首帧图片
  Prompt: "girl taking off her top"
  Output: 视频1

Segment 2:
  Node: PainterLongVideo
  Input 1: 视频1的最后一帧 (motion reference)
  Input 2: 首帧图片 (identity anchor)
  Prompt: "girl removing her pants"
  Output: 视频2 (保持身份一致)

最终: 拼接视频1 + 视频2
```

---

## 实际操作指南

### 使用 Chain Mode 重新生成

**步骤1**: 打开 Web UI，切换到"长视频生成"标签

**步骤2**: 配置参数
```
模型: A14B
预设: nsfw_v2
分辨率: 416x736
FPS: 16
```

**步骤3**: 添加两个 segment
```
Segment 1:
  Prompt: close-up shot of a girl in a dimly lit room, slowly taking off her top, her hands moving with confidence, her face showing a slight smile
  时长: 2秒

Segment 2:
  Prompt: the girl removing her pants, now undressed, her body relaxed, eyes looking directly at the camera
  时长: 2秒
```

**步骤4**: 启用自动续写
```
☑ 自动续写 (VLM 优化)
```

**步骤5**: 点击"生成全部"

---

### 使用 Story Mode 重新生成（推荐）

**步骤1**: 打开 Web UI，切换到"长视频生成"标签

**步骤2**: 上传首帧图片
```
点击"选择首帧图片"，上传角色的清晰照片
```

**步骤3**: 启用 Story 模式
```
☑ Story 模式 (身份一致性)
```

**步骤4**: 配置参数
```
模型: A14B
预设: nsfw_v2
分辨率: 416x736
FPS: 16
Motion Frames: 5
Boundary: 0.9
```

**步骤5**: 添加两个 segment（同上）

**步骤6**: 点击"生成全部"

---

## API 调用示例

### Chain Mode API

```bash
curl -X POST "http://localhost:8000/api/v1/generate/chain" \
  -H "X-API-Key: your-api-key" \
  -F "image=@first_frame.png" \
  -F 'params={
    "segments": [
      {
        "prompt": "close-up shot of a girl in a dimly lit room, slowly taking off her top, her hands moving with confidence, her face showing a slight smile",
        "duration": 2.0
      },
      {
        "prompt": "the girl removing her pants, now undressed, her body relaxed, eyes looking directly at the camera",
        "duration": 2.0
      }
    ],
    "model": "a14b",
    "model_preset": "nsfw_v2",
    "width": 416,
    "height": 736,
    "fps": 16,
    "auto_continue": true
  }'
```

### Story Mode API

```bash
curl -X POST "http://localhost:8000/api/v1/generate/chain" \
  -H "X-API-Key: your-api-key" \
  -F "image=@first_frame.png" \
  -F 'params={
    "segments": [
      {
        "prompt": "close-up shot of a girl in a dimly lit room, slowly taking off her top, her hands moving with confidence, her face showing a slight smile",
        "duration": 2.0
      },
      {
        "prompt": "the same girl removing her pants, now undressed, her body relaxed, eyes looking directly at the camera",
        "duration": 2.0
      }
    ],
    "model": "a14b",
    "model_preset": "nsfw_v2",
    "width": 416,
    "height": 736,
    "fps": 16,
    "story_mode": true,
    "motion_frames": 5,
    "boundary": 0.9,
    "clip_preset": "nsfw",
    "auto_continue": true
  }'
```

---

## 总结

### 问题根源
❌ **Prompt 使用了 Wan2.2 不支持的时间标记语法**
❌ **单个 prompt 包含多个矛盾的场景描述**
❌ **模型无法理解 `(at X seconds: ...)` 格式**

### 正确做法
✅ **使用单一连贯的动作描述**（适合简单短视频）
✅ **使用 Chain Mode 分段生成**（适合多场景视频）
✅ **使用 Story Mode 保持身份一致**（最佳方案）

### 关键要点
1. 不要在单个 prompt 中使用时间标记
2. 不要在单个 prompt 中描述多个独立场景
3. 使用 Chain/Story Mode 来实现复杂的场景变化
4. Story Mode 可以保持角色身份一致性

---

**报告日期**: 2026-03-03
**分析人**: Claude (Anthropic)
