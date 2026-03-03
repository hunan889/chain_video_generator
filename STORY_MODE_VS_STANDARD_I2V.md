# Story Mode vs 标准 I2V 对比

## 核心区别

### Story Mode Workflow
**使用节点**:
- `UNETLoader` (HIGH + LOW) - 使用 UNET 模型
- `CLIPLoader` - CLIP 文本编码器
- `VAELoader` - VAE 解码器
- `PainterI2V` - 专门的 I2V 节点
- `WanMoeKSamplerAdvanced` - 统一的高级采样器

**特点**:
- ✅ 统一采样：一个采样器处理 HIGH 和 LOW
- ✅ 更强的时序连贯性
- ✅ 专门设计用于处理复杂场景
- ✅ 可以更好地理解时间序列 prompt
- ✅ 身份一致性保持（多段视频）
- ✅ 场景过渡更自然

**适用场景**:
- 需要保持角色/物体身份一致的多段视频
- 复杂的场景变化
- 使用时间标记的 prompt（虽然模型不直接理解，但 workflow 处理更好）

---

### 标准 I2V Workflow
**使用节点**:
- `WanVideoModelLoader` (HIGH + LOW) - Wan2.2 专用模型加载器
- `LoadWanVideoT5TextEncoder` - T5 文本编码器
- `WanVideoVAELoader` - Wan2.2 VAE
- `WanVideoImageToVideoEncode` - 图像编码
- `WanVideoSampler` (x2) - 两阶段独立采样
- `WanVideoDecode` - 视频解码
- `WanVideoImageResizeToClosest` - 图像调整
- `VHS_VideoCombine` - 视频合成

**特点**:
- ✅ 标准的 Wan2.2 pipeline
- ✅ 适合单一场景、简单动作
- ✅ 生成速度略快
- ❌ 两阶段独立采样（HIGH 0-15, LOW 15-end）
- ❌ 对复杂 prompt 处理能力较弱
- ❌ 可能出现场景跳跃

**适用场景**:
- 简单的单一动作
- 单一场景描述
- 短视频（< 5秒）

---

## 技术对比

| 项目 | Story Mode | 标准 I2V |
|------|-----------|---------|
| **模型加载** | UNETLoader | WanVideoModelLoader |
| **文本编码** | CLIPLoader | LoadWanVideoT5TextEncoder |
| **I2V 节点** | PainterI2V | WanVideoImageToVideoEncode |
| **采样器** | WanMoeKSamplerAdvanced (统一) | WanVideoSampler x2 (分离) |
| **采样方式** | 单一采样器处理全程 | HIGH (0-15) → LOW (15-end) |
| **时序连贯性** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| **身份一致性** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| **复杂 prompt** | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| **生成速度** | ~7.4 分钟 | ~7.2 分钟 |
| **节点数量** | 13 | 10 |

---

## 采样器对比

### WanMoeKSamplerAdvanced (Story Mode)
```json
{
  "class_type": "WanMoeKSamplerAdvanced",
  "inputs": {
    "boundary": 0.9,
    "model_high_noise": ["30", 0],  // HIGH model
    "model_low_noise": ["31", 0],   // LOW model
    "positive": ["60", 0],
    "negative": ["60", 1],
    "latent_image": ["60", 2]
  }
}
```

**工作原理**:
- 在一个节点中处理 HIGH 和 LOW 模型
- 使用 `boundary` 参数控制切换点
- 统一的采样过程，避免断裂

---

### WanVideoSampler x2 (标准 I2V)
```json
// HIGH 阶段
{
  "class_type": "WanVideoSampler",
  "inputs": {
    "model": ["1", 0],  // HIGH model
    "start_step": 0,
    "end_step": 15
  }
}

// LOW 阶段
{
  "class_type": "WanVideoSampler",
  "inputs": {
    "model": ["2", 0],  // LOW model
    "samples": ["8", 0],  // 接收 HIGH 的输出
    "start_step": 15,
    "end_step": -1
  }
}
```

**工作原理**:
- 两个独立的采样器节点
- HIGH 阶段输出 → LOW 阶段输入
- 两阶段之间可能出现不连贯

---

## 为什么 Story Mode 更连贯？

### 1. 统一采样过程
- **Story Mode**: 一个采样器从头到尾处理，内部平滑过渡
- **标准 I2V**: 两个独立采样器，交接处可能出现跳跃

### 2. PainterI2V vs WanVideoImageToVideoEncode
- **PainterI2V**: 专门设计用于长视频和复杂场景
- **WanVideoImageToVideoEncode**: 标准的图像到视频编码

### 3. 模型架构
- **Story Mode**: 使用 UNET + CLIP（更强的语义理解）
- **标准 I2V**: 使用 Wan2.2 专用模型（优化速度）

---

## 使用建议

### 使用 Story Mode 的情况
✅ 多段视频需要保持角色一致性
✅ 复杂的场景变化
✅ 使用了时间标记的 prompt
✅ 需要更好的时序连贯性
✅ 对质量要求高于速度

### 使用标准 I2V 的情况
✅ 简单的单一动作
✅ 单一场景描述
✅ 短视频（< 5秒）
✅ 对速度要求高于质量
✅ 不需要身份一致性

---

## API 调用示例

### Story Mode (单段)
```json
{
  "prompt": "A girl slowly walking towards the camera",
  "model": "a14b",
  "model_preset": "nsfw_v2",
  "width": 416,
  "height": 736,
  "num_frames": 65,
  "fps": 16,
  "story_mode": true,
  "motion_frames": 5,
  "boundary": 0.9,
  "clip_preset": "nsfw"
}
```

### 标准 I2V
```json
{
  "prompt": "A girl slowly walking towards the camera",
  "model": "a14b",
  "model_preset": "nsfw_v2",
  "width": 416,
  "height": 736,
  "num_frames": 65,
  "fps": 16
}
```

**关键区别**: 添加 `story_mode: true` 参数

---

## 总结

### Story Mode
- **优势**: 更连贯、更好的身份一致性、适合复杂场景
- **劣势**: 略慢（+12秒）
- **推荐**: 需要高质量、复杂场景时使用

### 标准 I2V
- **优势**: 略快、标准 pipeline
- **劣势**: 可能出现场景跳跃、身份不一致
- **推荐**: 简单场景、单一动作时使用

---

**结论**: Story Mode 和标准 I2V 使用完全不同的 workflow 和节点，不是一样的！
