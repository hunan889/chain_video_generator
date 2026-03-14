# Advanced Workflow 阶段测试规范

## 概述

本文档定义 Advanced Workflow 各阶段的准入准出标准，用于分阶段测试和验证。

---

## Stage 1: Prompt 分析

### 准入条件 (Entry Criteria)

**必需输入：**
- `user_prompt`: string (用户原始提示词)
- `mode`: enum ("first_frame" | "face_reference" | "full_body_reference")

**可选输入：**
- `internal_config.stage1_prompt_analysis.auto_analyze`: boolean (default: true)
- `internal_config.stage1_prompt_analysis.auto_lora`: boolean (default: true)
- `internal_config.stage1_prompt_analysis.auto_prompt`: boolean (default: true)
- `internal_config.stage1_prompt_analysis.top_k_image_loras`: int (default: 5)
- `internal_config.stage1_prompt_analysis.top_k_video_loras`: int (default: 5)

**前置条件：**
- LLM 服务可用 (用于 prompt 优化)
- LoRA 推荐服务可用 (如果 auto_lora=true)

### 处理逻辑

1. 如果 `auto_analyze=false`，跳过此阶段，返回空结果
2. 如果 `auto_prompt=true`，调用 LLM 优化 prompt
3. 如果 `auto_lora=true`，调用 LoRA 推荐服务
4. 返回分析结果

### 准出条件 (Exit Criteria)

**成功输出：**
```json
{
  "original_prompt": "string",
  "optimized_t2i_prompt": "string | null",
  "optimized_i2v_prompt": "string | null",
  "image_loras": [
    {
      "name": "string",
      "trigger_words": ["string"],
      "score": 0.0-1.0
    }
  ],
  "video_loras": [
    {
      "name": "string",
      "trigger_words": ["string"],
      "score": 0.0-1.0
    }
  ]
}
```

**失败处理：**
- 如果 LLM 调用失败，使用原始 prompt
- 如果 LoRA 推荐失败，返回空数组
- 不阻断后续流程

**验证标准：**
- ✅ `optimized_t2i_prompt` 不为空（如果 auto_prompt=true）
- ✅ `image_loras` 数组长度 <= top_k_image_loras
- ✅ `video_loras` 数组长度 <= top_k_video_loras
- ✅ 所有 LoRA 的 score 在 0-1 范围内

---

## Stage 2: 首帧获取

### 准入条件 (Entry Criteria)

**必需输入：**
- `mode`: enum ("first_frame" | "face_reference" | "full_body_reference")
- `resolution`: string ("480p" | "720p" | "1080p")
- `aspect_ratio`: string ("16:9" | "9:16" | "3:4" | "4:3")

**条件输入：**
- 如果 `mode="first_frame"`:
  - `uploaded_first_frame`: string (URL, 必需)

- 如果 `mode="face_reference"` 或 `mode="full_body_reference"`:
  - `reference_image`: string (URL, 必需)
  - `internal_config.stage2_first_frame.first_frame_source`: enum ("generate" | "select_existing", default: "select_existing")

  - 如果 `first_frame_source="generate"`:
    - `internal_config.stage2_first_frame.t2i.steps`: int (default: 20)
    - `internal_config.stage2_first_frame.t2i.cfg_scale`: float (default: 7.0)
    - `internal_config.stage2_first_frame.t2i.sampler`: string (default: "DPM++ 2M Karras")
    - `internal_config.stage2_first_frame.t2i.seed`: int (default: -1)
    - `internal_config.stage2_first_frame.t2i.width`: int (计算自 resolution + aspect_ratio)
    - `internal_config.stage2_first_frame.t2i.height`: int (计算自 resolution + aspect_ratio)
    - Stage 1 的 `optimized_t2i_prompt` 或 `user_prompt`
    - Stage 1 的 `image_loras` (可选)

**可选输入：**
- `internal_config.stage2_first_frame.face_swap.enabled`: boolean (default: false)
- `internal_config.stage2_first_frame.face_swap.strength`: float (default: 1.0, range: 0-1)

**前置条件：**
- 如果 `first_frame_source="generate"`: ComfyUI 服务可用
- 如果 `first_frame_source="select_existing"`: 图片推荐服务可用
- 如果 `face_swap.enabled=true`: Reactor/Forge 服务可用

### 处理逻辑

1. **获取首帧：**
   - 如果 `mode="first_frame"`: 直接使用 `uploaded_first_frame`
   - 如果 `first_frame_source="generate"`: 调用 T2I 生成
   - 如果 `first_frame_source="select_existing"`: 从推荐图片中选择

2. **可选换脸：**
   - 如果 `face_swap.enabled=true` 且 `reference_image` 存在
   - 调用 Reactor API 对首帧进行换脸
   - 使用换脸后的图片作为最终首帧

### 准出条件 (Exit Criteria)

**成功输出：**
```json
{
  "first_frame_url": "string (URL)",
  "source": "uploaded | generated | selected",
  "face_swapped": boolean,
  "width": int,
  "height": int
}
```

**失败处理：**
- 如果 T2I 生成失败，抛出异常，终止流程
- 如果图片推荐失败，抛出异常，终止流程
- 如果换脸失败，记录警告，使用原始首帧继续

**验证标准：**
- ✅ `first_frame_url` 是有效的可访问 URL
- ✅ 图片尺寸符合 resolution + aspect_ratio 要求（误差 ±8px）
- ✅ 图片格式为 PNG 或 JPEG
- ✅ 如果 `face_swap.enabled=true`，`face_swapped=true`

---

## Stage 3: SeeDream 编辑

### 准入条件 (Entry Criteria)

**必需输入：**
- `mode`: enum ("first_frame" | "face_reference" | "full_body_reference")
- Stage 2 的 `first_frame_url`: string (URL)

**条件输入：**
- 如果 `mode="first_frame"`: 跳过此阶段
- 如果 `mode="face_reference"`:
  - `internal_config.stage3_seedream.enabled`: boolean (default: true, 可选)
- 如果 `mode="full_body_reference"`:
  - `internal_config.stage3_seedream.enabled`: boolean (必须为 true)

**可选输入：**
- `internal_config.stage3_seedream.mode`: enum ("face_only" | "face_wearings" | "full_body", default: "face_wearings")
- `internal_config.stage3_seedream.prompt`: string | null (自定义 prompt，null 则使用默认)
- `internal_config.stage3_seedream.enable_reactor`: boolean (default: true)
- `internal_config.stage3_seedream.strength`: float (default: 0.8, range: 0-1)
- `internal_config.stage3_seedream.seed`: int | null (default: null, 随机)
- `reference_image`: string (URL, 如果 enable_reactor=true 则必需)

**前置条件：**
- SeeDream 服务可用
- 如果 `enable_reactor=true`: Reactor/Forge 服务可用

### 处理逻辑

1. **检查是否执行：**
   - 如果 `mode="first_frame"`: 跳过
   - 如果 `enabled=false`: 跳过
   - 否则继续

2. **可选 Reactor 预处理：**
   - 如果 `enable_reactor=true` 且 `reference_image` 存在
   - 先对首帧进行换脸
   - 使用换脸后的图片作为 SeeDream 输入

3. **SeeDream 编辑：**
   - 确定 prompt（自定义或默认）
   - 调用 SeeDream API
   - image1 = reference_image, image2 = first_frame

### 准出条件 (Exit Criteria)

**成功输出：**
```json
{
  "edited_frame_url": "string (URL)",
  "mode": "face_only | face_wearings | full_body",
  "prompt_used": "string",
  "reactor_applied": boolean,
  "skipped": boolean
}
```

**失败处理：**
- 如果 SeeDream 调用失败，抛出异常，终止流程
- 如果 Reactor 预处理失败，记录警告，使用原始首帧继续

**验证标准：**
- ✅ 如果 `skipped=false`: `edited_frame_url` 是有效的可访问 URL
- ✅ 如果 `skipped=true`: 使用 Stage 2 的 `first_frame_url`
- ✅ 图片尺寸与输入首帧一致
- ✅ 如果 `enable_reactor=true`，`reactor_applied=true`

---

## Stage 4: 视频生成

### 准入条件 (Entry Criteria)

**必需输入：**
- `mode`: enum ("first_frame" | "face_reference" | "full_body_reference")
- `duration`: int (5 | 10 | 15, 秒)
- `resolution`: string ("480p" | "720p" | "1080p")
- `aspect_ratio`: string ("16:9" | "9:16" | "3:4" | "4:3")
- Stage 3 的 `edited_frame_url` 或 Stage 2 的 `first_frame_url`: string (URL)
- Stage 1 的 `optimized_i2v_prompt` 或 `user_prompt`: string

**可选输入：**
- `internal_config.stage4_video.generation.model`: enum ("A14B" | "5B", default: "A14B")
- `internal_config.stage4_video.generation.steps`: int (default: 20)
- `internal_config.stage4_video.generation.cfg`: float (default: 6.0)
- `internal_config.stage4_video.generation.shift`: float (default: 5.0)
- `internal_config.stage4_video.generation.scheduler`: enum ("unipc" | "euler" | "ddim", default: "unipc")
- `internal_config.stage4_video.generation.noise_aug_strength`: float (default: 0.05, range: 0-1)
- `internal_config.stage4_video.generation.motion_amplitude`: float (default: 0.0, range: 0-1)
- `internal_config.stage4_video.postprocess.upscale.enabled`: boolean (default: false)
- `internal_config.stage4_video.postprocess.upscale.model`: string (default: "RealESRGAN_x4plus")
- `internal_config.stage4_video.postprocess.upscale.resize`: float (default: 2.0)
- `internal_config.stage4_video.postprocess.interpolation.enabled`: boolean (default: false)
- `internal_config.stage4_video.postprocess.interpolation.multiplier`: int (2 | 4 | 8, default: 2)
- `internal_config.stage4_video.postprocess.interpolation.profile`: enum ("auto" | "fast" | "quality", default: "auto")
- Stage 1 的 `video_loras`: array (可选)
- `model_preset`: enum ("standard" | "nsfw_v2", 可选)

**前置条件：**
- ComfyUI 服务可用（A14B 或 5B 实例）
- Redis 可用（任务队列）

### 处理逻辑

1. **构建 Chain 请求：**
   - 使用 I2V 模式（所有模式统一使用 I2V）
   - 首帧来源：Stage 3 的 edited_frame 或 Stage 2 的 first_frame
   - Prompt: Stage 1 的 optimized_i2v_prompt 或 user_prompt
   - LoRAs: Stage 1 的 video_loras
   - 参数：从 internal_config.stage4_video.generation 获取

2. **调用 Chain 生成：**
   - 创建 AutoChainRequest
   - 提交到 task_manager
   - 轮询任务状态直到完成

3. **后处理（可选）：**
   - 如果 upscale.enabled=true: 应用超分
   - 如果 interpolation.enabled=true: 应用插帧

### 准出条件 (Exit Criteria)

**成功输出：**
```json
{
  "video_url": "string (URL)",
  "model": "A14B | 5B",
  "duration": int,
  "resolution": "480p | 720p | 1080p",
  "aspect_ratio": "16:9 | 9:16 | 3:4 | 4:3",
  "upscaled": boolean,
  "interpolated": boolean,
  "frame_count": int,
  "fps": int
}
```

**失败处理：**
- 如果视频生成失败，抛出异常，终止流程
- 如果后处理失败，记录警告，返回原始视频

**验证标准：**
- ✅ `video_url` 是有效的可访问 URL
- ✅ 视频时长符合 duration 要求（误差 ±0.5s）
- ✅ 视频分辨率符合 resolution + aspect_ratio 要求
- ✅ 视频格式为 MP4
- ✅ 视频可正常播放，无损坏帧

---

## 跨阶段数据流

```
Stage 1 Output → Stage 2 Input:
  - optimized_t2i_prompt (用于 T2I 生成)
  - image_loras (用于 T2I 生成)

Stage 1 Output → Stage 4 Input:
  - optimized_i2v_prompt (用于视频生成)
  - video_loras (用于视频生成)

Stage 2 Output → Stage 3 Input:
  - first_frame_url (SeeDream 的 image2)

Stage 3 Output → Stage 4 Input:
  - edited_frame_url (视频生成的首帧)
  - 如果 Stage 3 跳过，使用 Stage 2 的 first_frame_url
```

---

## 模式特定规则

### first_frame 模式
- Stage 1: 执行（可选）
- Stage 2: 使用 uploaded_first_frame，不执行换脸
- Stage 3: **跳过**
- Stage 4: 使用 uploaded_first_frame 作为首帧

### face_reference 模式
- Stage 1: 执行（可选）
- Stage 2: 生成或选择首帧，可选换脸
- Stage 3: **可选**（但至少 Stage 2 换脸或 Stage 3 之一必须启用）
- Stage 4: 使用 Stage 3 或 Stage 2 的首帧

### full_body_reference 模式
- Stage 1: 执行（可选）
- Stage 2: 生成或选择首帧，可选换脸
- Stage 3: **必选**（enabled 必须为 true）
- Stage 4: 使用 Stage 3 的首帧

---

## 测试建议

### 单元测试
每个阶段独立测试，mock 前置阶段的输出。

### 集成测试
按顺序执行多个阶段，验证数据流正确传递。

### 端到端测试
完整执行所有阶段，验证最终视频质量。

### 错误注入测试
在每个阶段注入失败，验证错误处理和回退逻辑。
