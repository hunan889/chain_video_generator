# Wan2.2 Video Generation Service — API 文档

> 基础地址: `http://{host}:8000`
> 认证方式: Header `X-API-Key: {api_key}`
> 默认 Key: `wan22-default-key-change-me`

---

## 目录

1. [视频生成 — 高级工作流](#1-视频生成--高级工作流)
2. [视频生成 — 基础接口](#2-视频生成--基础接口)
3. [视频扩展与链式生成](#3-视频扩展与链式生成)
4. [图片生成与编辑](#4-图片生成与编辑)
5. [换脸](#5-换脸)
6. [后处理](#6-后处理)
7. [任务管理](#7-任务管理)
8. [Prompt 优化](#8-prompt-优化)
9. [文字转语音 (TTS)](#9-文字转语音-tts)
10. [AI 对话](#10-ai-对话)
11. [LoRA 管理](#11-lora-管理)
12. [Pose 系统](#12-pose-系统)
13. [智能推荐](#13-智能推荐)
14. [资源与收藏](#14-资源与收藏)
15. [工作流历史](#15-工作流历史)
16. [DashScope 兼容接口](#16-dashscope-兼容接口)
17. [系统管理](#17-系统管理)
18. [前端对接状态总览](#18-前端对接状态总览)

---

## 1. 视频生成 — 高级工作流

### 1.1 分析 Prompt

分析用户输入, 推荐 LoRA 和参考图。

```
POST /api/v1/workflow/analyze
```

**请求体 (JSON)**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| prompt | string | 是 | - | 用户输入 (1-2000 字) |
| mode | string | 是 | - | `t2v` / `first_frame` / `face_reference` / `full_body_reference` |
| top_k_image_loras | int | 否 | 5 | 推荐图片 LoRA 数量 (1-20) |
| top_k_video_loras | int | 否 | 5 | 推荐视频 LoRA 数量 (1-20) |
| skip_prompt_optimization | bool | 否 | false | 跳过 prompt 优化 |

**响应体**:

```json
{
  "original_prompt": "a girl dancing",
  "optimized_t2i_prompt": "...",
  "optimized_i2v_prompt": "...",
  "image_loras": [
    {
      "lora_id": 1,
      "name": "lora_name",
      "description": "...",
      "trigger_words": ["word1"],
      "category": "style",
      "similarity": 0.85,
      "preview_url": "https://..."
    }
  ],
  "video_loras": [
    {
      "lora_id": 2,
      "name": "lora_name",
      "description": "...",
      "trigger_words": ["word1"],
      "trigger_prompt": "...",
      "mode": "I2V",
      "noise_stage": "high",
      "category": "action",
      "similarity": 0.9,
      "preview_url": "https://..."
    }
  ],
  "images": [],
  "mode": "t2v"
}
```

---

### 1.2 生成视频 (高级工作流)

多阶段视频生成: prompt 分析 → 首帧获取 → SeeDream 编辑 → 视频生成。

```
POST /api/v1/workflow/generate-advanced
```

**请求体 (JSON)**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| mode | string | 是 | - | `t2v` / `first_frame` / `face_reference` / `full_body_reference` |
| user_prompt | string | 是 | - | 用户文本提示 (1-2000 字) |
| resolution | string | 否 | null | `480p` / `720p` / `1080p` |
| aspect_ratio | string | 否 | null | `16:9` / `9:16` / `3:4` / `4:3` / `1:1` |
| duration | int | 否 | null | 视频时长 (秒) |
| uploaded_first_frame | string | 否 | null | 上传的首帧图片 (base64 data URL) |
| selected_image_url | string | 否 | null | 选择已有图片 URL 作为首帧 |
| reference_image | string | 否 | null | 参考图片 (face_reference / full_body_reference 模式) |
| pose_keys | list[string] | 否 | null | 指定 pose 关键字列表 |
| auto_analyze | bool | 否 | true | 自动分析 prompt |
| auto_lora | bool | 否 | true | 自动匹配 LoRA |
| auto_prompt | bool | 否 | true | 自动优化 prompt |
| turbo | bool | 否 | false | 使用 5B turbo 模型 |
| mmaudio | object | 否 | null | `{"enabled": true}` 开启 AI 音频 |
| parent_workflow_id | string | 否 | null | 续写的父工作流 ID |
| t2i_params | object | 否 | null | 覆盖文生图参数 |
| seedream_params | object | 否 | null | 覆盖 SeeDream 编辑参数 |
| video_params | object | 否 | null | 覆盖视频生成参数 |

**响应体**:

```json
{
  "workflow_id": "wf_abc123",
  "status": "queued",
  "current_stage": "prompt_analysis",
  "stages": [
    {"name": "prompt_analysis", "status": "pending"},
    {"name": "first_frame_acquisition", "status": "pending"},
    {"name": "video_generation", "status": "pending"}
  ]
}
```

---

### 1.3 查询工作流状态

轮询工作流进度, 直到 `status` 为 `completed` 或 `failed`。

```
GET /api/v1/workflow/status/{workflow_id}
```

**Query 参数**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| detail | bool | false | 返回详细阶段信息 |

**响应体**:

```json
{
  "workflow_id": "wf_abc123",
  "status": "completed",
  "current_stage": "video_generation",
  "progress": 1.0,
  "video_progress": 1.0,
  "current_step": 5,
  "max_step": 5,
  "final_video_url": "https://cos-bucket.com/videos/xxx.mp4",
  "first_frame_url": "https://cos-bucket.com/uploads/xxx.png",
  "edited_frame_url": "https://cos-bucket.com/uploads/yyy.png",
  "elapsed_time": 45.2,
  "user_prompt": "a girl dancing",
  "mode": "t2v",
  "created_at": 1711440000,
  "completed_at": 1711440045,
  "error": null,
  "stages": [
    {"name": "prompt_analysis", "status": "completed"},
    {"name": "first_frame_acquisition", "status": "completed"},
    {"name": "video_generation", "status": "completed"}
  ]
}
```

**状态值**: `queued` → `running` → `completed` / `failed`

**stage 值**: `prompt_analysis`, `first_frame_acquisition`, `seedream_edit`, `video_generation`

---

### 1.4 取消工作流

```
POST /api/v1/workflow/{workflow_id}/cancel
```

**响应体**:

```json
{"status": "cancelled", "workflow_id": "wf_abc123"}
```

---

### 1.5 SeeDream 编辑

独立调用 SeeDream 图片编辑 (换脸 + 风格编辑)。

```
POST /api/v1/workflow/seedream-edit
```

**请求体 (JSON)**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| scene_image | string | 是 | - | 场景图 (base64 或 URL) |
| reference_face | string | 否 | null | 参考人脸 (base64 或 URL) |
| mode | string | 否 | null | `face_only` / `face_wearings` / `full_body` |
| enable_face_swap | bool | 否 | true | 是否换脸 |
| prompt | string | 否 | null | 编辑提示词 |
| size | string | 否 | "1024x1024" | 输出尺寸 |
| seed | int | 否 | null | 随机种子 |

**响应体**:

```json
{
  "url": "https://cos-bucket.com/uploads/xxx.png",
  "edit_mode": "face_only",
  "face_swapped": true,
  "size": "1024x1024",
  "seed": 12345
}
```

---

### 1.6 获取默认配置

```
GET /api/v1/workflow/default-config
```

**Query 参数**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| mode | string | "t2v" | 模式 |
| turbo | bool | false | 是否 turbo |
| resolution | string | "720p" | 分辨率 |

---

## 2. 视频生成 — 基础接口

### 2.1 文生视频 (T2V)

```
POST /api/v1/generate
```

**请求体 (JSON 或 FormData)**:

JSON 模式:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | 是 | - | `a14b` / `5b` |
| prompt | string | 是 | - | 视频描述 |
| negative_prompt | string | 否 | null | 负面提示 |
| width | int | 否 | 832 | 宽度 |
| height | int | 否 | 480 | 高度 |
| num_frames | int | 否 | 81 | 帧数 |
| fps | int | 否 | 24 | 帧率 |
| steps | int | 否 | 20 | 推理步数 |
| cfg | float | 否 | 6.0 | CFG 引导强度 |
| shift | float | 否 | 5.0 | Shift 值 |
| seed | int | 否 | null | 随机种子 |
| scheduler | string | 否 | "unipc" | 调度器 |
| loras | list | 否 | [] | LoRA 列表 `[{"name": "xxx", "strength": 0.8}]` |
| auto_lora | bool | 否 | false | 自动匹配 LoRA |
| auto_prompt | bool | 否 | false | 自动优化 prompt |
| model_preset | string | 否 | "" | 模型预设 |
| upscale | bool | 否 | false | 是否超分 |
| t5_preset | string | 否 | "" | T5 预设 |

FormData 模式: `params` (JSON 字符串) + 可选 `face_image` (File)

**响应体**:

```json
{
  "task_id": "abc123",
  "status": "queued"
}
```

---

### 2.2 图生视频 (I2V)

```
POST /api/v1/generate/i2v
Content-Type: multipart/form-data
```

**FormData 字段**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| image | File | 是 | 首帧图片 |
| params | string | 是 | JSON 字符串, 同 T2V 参数 + 额外字段 |
| face_image | File | 否 | 换脸参考图 |

**params 额外字段**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| noise_aug_strength | float | 0.0 | 噪声增强 |
| motion_amplitude | float | 0.0 | 运动幅度 |
| color_match | bool | true | 颜色匹配 |
| color_match_method | string | "mkl" | 颜色匹配方法 |
| resize_mode | string | "crop_to_new" | 缩放模式 |

**响应体**: 同 T2V

---

## 3. 视频扩展与链式生成

### 3.1 视频续写

```
POST /api/v1/generate/extend
```

**请求体 (JSON)**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| parent_task_id | string | 是 | - | 父任务 ID |
| prompt | string | 是 | - | 续写描述 |
| negative_prompt | string | 否 | null | 负面提示 |
| num_frames | int | 否 | null | 帧数 |
| steps | int | 否 | null | 步数 |
| cfg | float | 否 | null | CFG |
| seed | int | 否 | null | 种子 |
| auto_prompt | bool | 否 | false | 自动优化 |
| noise_aug_strength | float | 否 | 0.0 | 噪声增强 |
| concat_with_parent | bool | 否 | true | 是否与父视频拼接 |

---

### 3.2 链式视频生成

自动分段生成长视频。

```
POST /api/v1/generate/chain
Content-Type: multipart/form-data
```

**FormData 字段**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| params | string | 是 | JSON 字符串 (AutoChainRequest) |
| image | File | 否 | 首帧图片 |
| face_image | File | 否 | 换脸参考图 |
| initial_reference_image | File | 否 | 初始参考图 |

**params 关键字段**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| model | string | - | `a14b` / `5b` |
| segments | list | null | `[{"prompt": "...", "duration": 5, "loras": []}]` |
| prompt | string | null | 旧版单 prompt |
| image_mode | string | - | `none` / `first_frame` / `face_reference` / `full_body_reference` |
| story_mode | bool | true | 故事模式 (合并工作流) |
| auto_continue | bool | false | 自动续写 |
| parent_chain_id | string | null | 续写的父链 ID |
| enable_upscale | bool | false | 超分 |
| enable_interpolation | bool | false | 插帧 |
| enable_mmaudio | bool | false | AI 音频 |

**响应体**:

```json
{
  "chain_id": "abc123",
  "total_segments": 3,
  "status": "queued"
}
```

---

### 3.3 查询链状态

```
GET /api/v1/chains/{chain_id}
```

**响应体**:

```json
{
  "chain_id": "abc123",
  "total_segments": 3,
  "completed_segments": 1,
  "current_task_progress": 0.5,
  "status": "running",
  "final_video_url": null,
  "error": null
}
```

---

### 3.4 列出所有链

```
GET /api/v1/chains
```

---

### 3.5 取消链

```
POST /api/v1/chains/{chain_id}/cancel
```

---

### 3.6 合并视频段

```
POST /api/v1/generate/merge-segments
```

**请求体**: `{"segment_task_ids": ["task1", "task2", ...]}`

**响应体**: `{"video_url": "...", "segment_count": 3, "message": "..."}`

---

## 4. 图片生成与编辑

### 4.1 上传图片

```
POST /api/v1/image/upload
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | File | 是 | 图片文件 |

**响应体**: `{"url": "https://...", "filename": "xxx.png"}`

---

### 4.2 文生图 (T2I)

```
POST /api/v1/image/generate
```

**请求体 (JSON)**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| prompt | string | 是 | - | 图片描述 |
| size | string | 否 | "2048x2048" | `1K` / `2K` / `4K` 或 `WxH` |
| seed | int | 否 | null | 随机种子 |
| negative_prompt | string | 否 | null | 负面提示 |
| model | string | 否 | null | 模型 ID |

**响应体**:

```json
{
  "url": "https://cos-bucket.com/uploads/xxx.png",
  "size": "2048x2048",
  "seed": 12345
}
```

---

### 4.3 图片编辑 (I2I)

```
POST /api/v1/image/edit
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| image | File | 是 | - | 原图 |
| prompt | string | 是 | - | 编辑指令 |
| size | string | 否 | "1K" | 输出尺寸 |
| seed | int | 否 | null | 随机种子 |
| model | string | 否 | null | 模型 ID |

**响应体**: 同 T2I

---

### 4.4 多参考图生成

```
POST /api/v1/image/multiref
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| images | File[] | 是 | - | 参考图片列表 |
| prompt | string | 是 | - | 描述 |
| size | string | 否 | "1024x1024" | 输出尺寸 |
| seed | int | 否 | null | 种子 |
| model | string | 否 | null | 模型 |

**响应体**: 同 T2I

---

### 4.5 Z-Image 无限制编辑

基于 ComfyUI 的图片编辑。

```
POST /api/v1/image/zimage-edit
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| image | File | 是 | - | 原图 |
| prompt | string | 是 | - | 编辑指令 |
| negative_prompt | string | 否 | "" | 负面提示 |
| denoise | float | 否 | 0.7 | 去噪强度 |
| steps | int | 否 | 4 | 步数 |
| cfg | float | 否 | 1.0 | CFG |
| seed | int | 否 | null | 种子 |
| controlnet_strength | float | 否 | 0.5 | ControlNet 强度 |

---

### 4.6 角色一致性生成

InstantID + FaceID + IP-Adapter 组合。

```
POST /api/v1/image/character-consistency
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| face_image | File | 是 | - | 人脸参考图 |
| prompt | string | 是 | - | 描述 |
| width | int | 否 | 768 | 宽度 |
| height | int | 否 | 1024 | 高度 |
| steps | int | 否 | 30 | 步数 |
| cfg | float | 否 | 3.5 | CFG |
| seed | int | 否 | null | 种子 |
| instantid_weight | float | 否 | 0.85 | InstantID 权重 |
| faceid_weight | float | 否 | 0.85 | FaceID 权重 |
| ipadapter_weight | float | 否 | 0.3 | IP-Adapter 权重 |

---

### 4.7 图片历史

```
GET /api/v1/image/history
```

**Query 参数**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| page | int | 1 | 页码 |
| page_size | int | 20 | 每页数量 |
| generation_type | string | null | 过滤类型 |

**响应体**:

```json
{
  "items": [
    {
      "id": 1,
      "generation_type": "edit",
      "prompt": "...",
      "image_url": "https://...",
      "size": "1024x1024",
      "created_at": "2024-01-01T00:00:00"
    }
  ],
  "total": 100,
  "page": 1,
  "page_size": 20,
  "total_pages": 5
}
```

---

## 5. 换脸

### 5.1 图片换脸 (Scene Swap)

Reactor 快速换脸 + 可选 SeedDream 精修。

```
POST /api/v1/image/scene-swap
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| face_image | File | 是 | - | 人脸源图 (≤10MB) |
| scene_image | File | 是 | - | 目标场景图 (≤10MB) |
| reactor_only | string | 否 | "false" | `"true"` 跳过 SeedDream, 只用 Reactor |
| skip_reactor | string | 否 | "false" | `"true"` 跳过 Reactor, 只用 SeedDream |
| expression_keep | float | 否 | 0.0 | 保留原表情 (0-1) |
| preserve_occlusion | string | 否 | "false" | 保留遮挡物 |
| size | string | 否 | "1024x1024" | 输出尺寸 |
| seed | int | 否 | null | 种子 |
| prompt | string | 否 | (默认换脸 prompt) | SeedDream 编辑提示 |
| extra_prompt | string | 否 | null | 附加提示 |
| reactor_codeformer_weight | float | 否 | 0.7 | CodeFormer 修复权重 |
| reactor_restorer_visibility | float | 否 | 1.0 | 修复可见度 |
| reactor_det_thresh | float | 否 | 0.5 | 人脸检测阈值 |

**响应体**:

```json
{
  "url": "https://cos-bucket.com/uploads/xxx.png",
  "size": "1024x1024",
  "seed": null,
  "cropped_inputs": ["https://...", "https://..."]
}
```

---

### 5.2 图片换脸 (Forge - InstantID)

```
POST /api/v1/image/faceswap
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| faces | File[] | 是 | - | 人脸参考图 (1-3张) |
| prompt | string | 否 | "" | 描述 |
| width | int | 否 | 768 | 宽度 |
| height | int | 否 | 1024 | 高度 |
| steps | int | 否 | 30 | 步数 |
| cfg_scale | float | 否 | 3.5 | CFG |
| face_weight | float | 否 | 0.85 | 人脸权重 |
| seed | int | 否 | null | 种子 |

---

### 5.3 姿态转换

```
POST /api/v1/image/transfer
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| ref_image | File | 是 | - | 参考人物图 |
| pose_image | File | 否 | null | 姿态图 |
| prompt | string | 否 | "" | 描述 |
| structure_mode | string | 否 | "openpose" | 结构模式 |
| structure_weight | float | 否 | 0.65 | 结构权重 |
| pose_weight | float | 否 | 0.75 | 姿态权重 |
| face_weight | float | 否 | 0.85 | 人脸权重 |
| enable_face | bool | 否 | true | 启用 InstantID |
| enable_appearance | bool | 否 | true | 启用 IP-Adapter |

---

### 5.4 视频换脸

异步任务, 需轮询。

```
POST /api/v1/video/faceswap
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| face_image | File | 是 | 人脸源图 |
| video | File | 是 | 目标视频 |
| faces_index | string | 否 | 换脸索引 |

**响应体**:

```json
{
  "task_id": "abc123",
  "status": "queued"
}
```

使用 [7.2 查询任务状态](#72-查询任务状态) 轮询, 完成后 `video_url` 可用。

---

## 6. 后处理

### 6.1 视频插帧

RIFE TensorRT 帧插值。

```
POST /api/v1/postprocess/interpolate
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| video | File | 否* | - | 视频文件 |
| task_id | string | 否* | null | 或提供已有任务 ID |
| multiplier | int | 否 | 2 | 插帧倍数 |
| resolution_profile | string | 否 | "small" | `small` / `fast` |
| fps | float | 否 | 16.0 | 输出帧率 |

> *video 和 task_id 二选一

**响应体**: `{"task_id": "abc123", "message": "Interpolation started"}`

---

### 6.2 视频超分

```
POST /api/v1/postprocess/upscale
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| video | File | 否* | - | 视频文件 |
| task_id | string | 否* | null | 已有任务 ID |
| model | string | 否 | "4x_foolhardy_Remacri" | 超分模型 |
| resize_to | string | 否 | "FHD" | 目标分辨率 |
| fps | float | 否 | 16.0 | 帧率 |

---

### 6.3 图片超分

```
POST /api/v1/postprocess/upscale-image
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| image | File | 是 | - | 图片文件 |
| model | string | 否 | "RealESRGAN_x2plus.pth" | 超分模型 |

**响应体**: `{"url": "...", "filename": "...", "model": "..."}`

---

### 6.4 AI 音频

MMAudio 为视频添加音效。

```
POST /api/v1/postprocess/audio
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| video | File | 否* | - | 视频文件 |
| task_id | string | 否* | null | 已有任务 ID |
| prompt | string | 否 | "" | 音频描述 |
| negative_prompt | string | 否 | "" | 负面音频描述 |
| steps | int | 否 | 25 | 步数 |
| cfg | float | 否 | 4.5 | CFG |

---

## 7. 任务管理

### 7.1 列出所有任务

```
GET /api/v1/tasks
```

**响应体**: `[TaskResponse, ...]`

---

### 7.2 查询任务状态

```
GET /api/v1/tasks/{task_id}
```

**响应体**:

```json
{
  "task_id": "abc123",
  "mode": "t2v",
  "model": "a14b",
  "status": "completed",
  "progress": 1.0,
  "video_url": "/api/v1/results/video_xxx.mp4",
  "created_at": 1711440000,
  "completed_at": 1711440045,
  "error": null
}
```

**状态值**: `queued` → `running` → `completed` / `failed` / `cancelled`

---

### 7.3 取消任务

```
POST /api/v1/tasks/{task_id}/cancel
```

---

### 7.4 获取结果文件

```
GET /api/v1/results/{filename}
```

直接返回文件 (视频/图片/音频), 无需认证。

---

## 8. Prompt 优化

### 8.1 优化 Prompt

```
POST /api/v1/prompt/optimize
```

**请求体 (JSON)**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| prompt | string | 是 | - | 原始 prompt |
| lora_names | list[string] | 否 | null | 使用的 LoRA 名称 |
| mode | string | 否 | "t2v" | `t2v` / `i2v` |
| image_base64 | string | 否 | null | 参考图片 (I2V 模式) |
| duration | float | 否 | null | 视频时长 |

**响应体**:

```json
{
  "original_prompt": "a girl dancing",
  "optimized_prompt": "(at 0 seconds: ...) (at 3 seconds: ...)",
  "trigger_words_used": ["word1"],
  "explanation": "优化说明"
}
```

---

### 8.2 图片描述

使用 VLM 描述图片内容。

```
POST /api/v1/prompt/describe-image
```

**请求体 (JSON)**: `{"image_base64": "data:image/png;base64,..."}`

**响应体**: `{"description": "A woman standing in a garden..."}`

---

## 9. 文字转语音 (TTS)

### 9.1 生成语音

ChatTTS 文字转语音。

```
POST /api/v1/tts
```

**请求体 (JSON)**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| text | string | 是 | - | 要朗读的文字 |
| seed | int | 否 | null | 语音种子 (固定音色) |
| temperature | float | 否 | 0.3 | 温度 (0.01-1.0) |
| top_p | float | 否 | 0.7 | Top-P (0.1-1.0) |
| speed | int | 否 | 5 | 语速 (0-9, 5=正常) |
| oral | int | 否 | 0 | 口语化 (0-9) |
| laugh | int | 否 | 0 | 笑声 (0-2) |
| pause | int | 否 | 3 | 停顿 (0-7) |

**响应体**:

```json
{
  "audio_file": "/api/v1/results/tts_xxx.wav",
  "duration": 3.5,
  "sample_rate": 24000,
  "filename": "tts_xxx.wav"
}
```

---

### 9.2 语音种子列表

```
GET /api/v1/tts/voices
```

**响应体**: `{"info": "...", "suggested_seeds": [...], "note": "..."}`

---

## 10. AI 对话

### 10.1 Chat Completions

代理 BytePlus ModelArk LLM, 支持 SSE 流式。

```
POST /api/v1/chat/completions
```

**请求体 (JSON)**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| messages | list | 是 | - | `[{"role": "user", "content": "..."}]` |
| model | string | 否 | null | 模型 ID |
| stream | bool | 否 | true | 流式返回 |
| tools | list | 否 | null | MCP 工具 |

**响应**: `text/event-stream` (SSE) 或 JSON

---

## 11. LoRA 管理

### 11.1 列出 LoRA

```
GET /api/v1/loras
```

**响应体**:

```json
[
  {
    "id": 1,
    "name": "wan_22_doggy_by_mq_lab",
    "description": "Doggy style position",
    "trigger_words": ["mqldgy_a", "on all fours"],
    "trigger_prompt": "A woman and a man...",
    "category": "action",
    "preview_url": "https://..."
  }
]
```

---

### 11.2 LoRA 推荐

```
POST /api/v1/loras/recommend
```

**请求体**: `{"prompt": "...", "top_k": 5}`

---

### 11.3 下载 LoRA

```
POST /api/v1/loras/download
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| url | string | 是 | 下载地址 |
| filename | string | 否 | 文件名 |
| token | string | 否 | 认证 token |

---

### 11.4 查询下载状态

```
GET /api/v1/loras/download/{dl_id}
```

---

### 11.5 列出 LoRA 文件

```
GET /api/v1/loras/files
```

**响应体**: `[{"name": "xxx.safetensors", "size_mb": 180.5}]`

---

## 12. Pose 系统

### 12.1 列出所有 Pose

```
GET /api/v1/poses
```

**Query 参数**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| category | string | null | 按类别过滤 |
| include_disabled | bool | false | 包含禁用的 |

**响应体**:

```json
{
  "poses": [
    {
      "id": 1,
      "pose_key": "doggy",
      "name_cn": "后入",
      "name_en": "Doggy",
      "category": "action",
      "enabled": true,
      "thumbnail_url": "..."
    }
  ]
}
```

---

### 12.2 获取 Pose 配置

```
GET /api/v1/poses/{pose_id}/config
```

**Query 参数**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| angle | string | null | 角度筛选 |
| style | string | null | 风格筛选 |
| noise_stage | string | "high" | 噪声阶段 |

**响应体**:

```json
{
  "pose": {"id": 1, "pose_key": "doggy", "name_cn": "后入", ...},
  "reference_images": [{"id": 1, "url": "...", "angle": "side", ...}],
  "image_loras": [{"lora_id": 1, "name": "...", "weight": 0.8, ...}],
  "video_loras": [{"lora_id": 2, "name": "...", "weight": 0.8, ...}],
  "prompt_templates": [{"template": "...", "type": "i2v"}]
}
```

---

### 12.3 批量获取 Pose 配置

```
POST /api/v1/poses/batch-config
```

**请求体**: `{"pose_ids": [1, 2, 3]}`

---

### 12.4 Pose 推荐

```
POST /api/v1/poses/recommend
```

**请求体**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| prompt | string | 是 | - | 用户描述 |
| top_k | int | 否 | 5 | 推荐数量 |
| use_llm | bool | 否 | true | 使用 LLM 重排 |
| use_embedding | bool | 否 | true | 使用向量搜索 |

**响应体**:

```json
{
  "recommendations": [
    {
      "pose_key": "doggy",
      "name_cn": "后入",
      "name_en": "Doggy",
      "score": 0.95,
      "match_reason": "...",
      "category": "action"
    }
  ]
}
```

---

### 12.5 推荐工作流

根据 prompt + 选中 pose 推荐完整工作流配置。

```
POST /api/v1/poses/recommend-workflow
```

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| prompt | string | 是 | 用户描述 |
| pose_keys | list[string] | 是 | 选中的 pose |

**响应体**:

```json
{
  "optimized_prompt": "...",
  "reference_image": "https://...",
  "image_loras": [{"lora_id": 1, "lora_name": "...", "weight": 0.8}],
  "video_loras": [{"lora_id": 2, "lora_name": "...", "weight": 0.8}],
  "image_prompt": "...",
  "video_prompt": "..."
}
```

---

### 12.6 Pose 缩略图

```
GET /api/v1/poses/{pose_key}/thumbnail
```

---

### 12.7 Pose 图片文件

```
GET /pose-files/{pose}/{filename}
```

直接返回图片/视频文件。

---

## 13. 智能推荐

### 13.1 综合推荐

语义搜索 + LLM 推荐参考图和 LoRA。

```
POST /api/v1/recommend
```

**请求体 (JSON)**:

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| prompt | string | 是 | - | 用户描述 |
| mode | string | 否 | null | `I2V` / `T2V` / `both` |
| include_images | bool | 否 | true | 包含参考图 |
| include_loras | bool | 否 | true | 包含 LoRA |
| top_k_images | int | 否 | 5 | 图片数量 |
| top_k_loras | int | 否 | 5 | LoRA 数量 |
| min_similarity | float | 否 | 0.6 | 最低相似度 |

**响应体**:

```json
{
  "images": [
    {"resource_id": 1, "prompt": "...", "url": "...", "similarity": 0.85}
  ],
  "video_loras": [
    {"lora_id": 1, "name": "...", "mode": "I2V", "similarity": 0.9}
  ],
  "image_loras": [
    {"lora_id": "img_1", "name": "...", "similarity": 0.8}
  ]
}
```

---

## 14. 资源与收藏

### 14.1 资源列表

```
GET /api/v1/resources
```

**Query 参数**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| page | int | 1 | 页码 |
| page_size | int | 12 | 每页数量 |
| resource_type | string | null | `image` / `video` |
| tag | string | null | 标签过滤 |
| search | string | null | 搜索关键词 |

**响应体**:

```json
{
  "resources": [
    {
      "id": 1,
      "resource_type": "image",
      "url": "https://...",
      "prompt": "...",
      "tags": [{"tag_id": 1, "name": "portrait", "category": "style"}],
      "is_favorited": false
    }
  ],
  "total": 100,
  "page": 1,
  "page_size": 12,
  "total_pages": 9
}
```

---

### 14.2 多标签搜索

```
GET /api/v1/resources/search
```

**Query 参数**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| tags | string | - | 逗号分隔标签 |
| match_mode | string | "all" | `all` / `any` |
| page | int | 1 | 页码 |
| page_size | int | 12 | 每页 |
| resource_type | string | null | 类型过滤 |

---

### 14.3 资源详情

```
GET /api/v1/resources/{resource_id}
```

---

### 14.4 上传资源

```
POST /api/v1/resources/upload
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | File | 是 | 文件 |
| resource_type | string | 是 | `image` / `video` |
| prompt | string | 否 | 描述 |

---

### 14.5 添加标签

```
POST /api/v1/resources/{resource_id}/tags
```

**请求体**: `{"tag_name": "portrait", "category": "style"}`

---

### 14.6 删除标签

```
DELETE /api/v1/resources/{resource_id}/tags/{tag_id}
```

---

### 14.7 标签列表

```
GET /api/v1/tags
```

**Query**: `category` (可选)

---

### 14.8 收藏 — 添加

```
POST /api/v1/favorites/{resource_id}
```

**请求体**: `{"note": "optional note"}`

---

### 14.9 收藏 — 删除

```
DELETE /api/v1/favorites/{resource_id}
```

---

### 14.10 收藏 — 列表

```
GET /api/v1/favorites
```

**Query**: `page=1, page_size=12, resource_type=null, search=null`

**响应体**: 同资源列表格式

---

### 14.11 收藏 — 检查

```
GET /api/v1/favorites/check/{resource_id}
```

**响应体**: `{"is_favorited": true}`

---

### 14.12 收藏 — 统计

```
GET /api/v1/favorites/stats
```

**响应体**: `{"total": 50, "image": 30, "video": 10, "pose_image": 5, "video_lora": 3, "image_lora": 2}`

---

### 14.13 收藏 — 全部 (含 Pose 图/LoRA)

```
GET /api/v1/favorites/all
```

**Query**: `page=1, page_size=50, favorite_type=null`

---

### 14.14 Pose 图片收藏

```
POST /api/v1/favorites/pose-image          # 添加
DELETE /api/v1/favorites/pose-image         # 删除 (Query: resource_path)
GET /api/v1/favorites/pose-image/check     # 检查 (Query: resource_path)
```

---

### 14.15 LoRA 收藏

```
POST /api/v1/favorites/lora                # 添加 (Body: lora_id, lora_type)
DELETE /api/v1/favorites/lora              # 删除 (Query: lora_id, lora_type)
GET /api/v1/favorites/lora/check           # 检查 (Query: lora_id, lora_type)
```

---

## 15. 工作流历史

### 15.1 保存到历史

```
POST /api/v1/workflow/history/save
```

---

### 15.2 历史列表

```
GET /api/v1/workflow/history
```

**Query 参数**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| page | int | 1 | 页码 |
| page_size | int | 24 | 每页 |
| status | string | null | `completed` / `failed` |

**响应体**:

```json
{
  "workflows": [
    {
      "workflow_id": "wf_abc123",
      "status": "completed",
      "mode": "t2v",
      "user_prompt": "...",
      "final_video_url": "https://...",
      "first_frame_url": "https://...",
      "created_at": 1711440000,
      "completed_at": 1711440045
    }
  ],
  "total": 50,
  "page": 1,
  "page_size": 24
}
```

---

### 15.3 历史详情

```
GET /api/v1/workflow/history/{history_id}
```

---

### 15.4 删除历史

```
DELETE /api/v1/workflow/history/{history_id}
```

---

### 15.5 重新运行

```
POST /api/v1/workflow/history/{history_id}/rerun
```

---

## 16. DashScope 兼容接口

兼容阿里 DashScope 视频生成 API 格式。

### 16.1 提交任务

```
POST /api/v1/dashscope/video-generation
```

**认证**: `X-API-Key` 或 `Authorization: Bearer {key}`

**请求体**:

```json
{
  "model": "wanx2.1-t2v-turbo",
  "input": {
    "prompt": "a girl dancing",
    "img_url": null
  },
  "parameters": {
    "size": "1920*1080",
    "duration": 5,
    "resolution": "720p",
    "aspect_ratio": "16:9",
    "turbo": true,
    "mmaudio_enabled": false
  }
}
```

---

### 16.2 查询任务

```
GET /api/v1/dashscope/tasks/{task_id}
```

**响应体**:

```json
{
  "request_id": "...",
  "output": {
    "task_id": "wf_abc123",
    "task_status": "SUCCEEDED",
    "video_url": "https://...",
    "progress": 1.0,
    "current_stage": "video_generation",
    "first_frame_url": "https://..."
  },
  "usage": {}
}
```

**task_status 值**: `PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED`

---

## 17. 系统管理

### 17.1 健康检查

```
GET /health
```

无需认证。

**响应体**: `{"status": "ok", "comfyui": {...}, "redis": {...}}`

---

### 17.2 模型预设

```
GET /api/v1/model-presets
GET /api/v1/t5-presets
```

---

### 17.3 媒体代理

解决 CORS 问题, 代理外部媒体。

```
GET /api/v1/proxy-media?url={encoded_url}
```

---

### 17.4 GPU 状态 (Admin)

```
GET /api/v1/admin/gpu-status
```

---

### 17.5 Worker 管理 (Admin)

```
GET /api/v1/admin/workers              # 列出 ComfyUI workers
POST /api/v1/admin/workers             # 添加 worker
DELETE /api/v1/admin/workers/{id}      # 删除 worker
```

---

### 17.6 系统设置 (Admin)

```
GET /api/v1/admin/settings
PUT /api/v1/admin/settings
```

---

### 17.7 Embedding 索引管理 (Admin)

```
GET /api/v1/admin/embeddings/stats
POST /api/v1/admin/embeddings/rebuild-favorites
POST /api/v1/admin/embeddings/rebuild-loras
DELETE /api/v1/admin/embeddings/clear-all
```

---

### 17.8 CivitAI 集成 (Admin)

```
GET /api/v1/civitai/search             # 搜索 CivitAI
GET /api/v1/civitai/models/{id}        # 模型详情
POST /api/v1/civitai/download          # 下载
POST /api/v1/civitai/sync-examples     # 同步示例 prompt
```

---

## 18. 前端对接状态总览

### 已对接

| 前端页面 | 接口 | 说明 |
|---------|------|------|
| AI Video (T2V) | `POST /workflow/generate-advanced` + `GET /workflow/status/{id}` | mode=t2v |
| AI Video (I2V) | `POST /workflow/generate-advanced` + `GET /workflow/status/{id}` | mode=first_frame, uploaded_first_frame |
| AI Video (续写) | `POST /workflow/generate-advanced` + `GET /workflow/status/{id}` | parent_workflow_id |
| Photo Alive | `POST /workflow/generate-advanced` + `GET /workflow/status/{id}` | mode=first_frame |
| AI Face (图片) | `POST /image/scene-swap` | reactor_only=true |
| AI Face (视频) | `POST /video/faceswap` + `GET /tasks/{id}` | 异步轮询 |
| AI Photo Edit | `POST /image/edit` | 同步返回 |

### 未对接 (后端已有)

| 前端页面 | 可用接口 | 说明 |
|---------|----------|------|
| My Videos | `GET /workflow/history` | 视频历史列表 |
| My Images | `GET /image/history` | 图片历史列表 |
| Favorites | `GET/POST/DELETE /favorites/*` | 完整收藏系统 |
| Gallery | `GET /resources` + `POST /recommend` | 资源浏览 + 推荐 |

### 未对接 (后端能力未利用)

| 能力 | 接口 | 前端可用场景 |
|------|------|-------------|
| 文生图 | `POST /image/generate` | 独立 T2I 工具页 |
| Prompt 优化 | `POST /prompt/optimize` | 提示词助手 |
| 图片描述 | `POST /prompt/describe-image` | 以图生文 |
| 视频插帧 | `POST /postprocess/interpolate` | 后处理选项 |
| 视频超分 | `POST /postprocess/upscale` | 后处理选项 |
| 图片超分 | `POST /postprocess/upscale-image` | 后处理选项 |
| AI 音频 | `POST /postprocess/audio` | 视频配音 |
| 文字转语音 | `POST /tts` | 配音/旁白 |
| AI 对话 | `POST /chat/completions` | 对话助手 |
| LoRA 列表 | `GET /loras` | 风格/动作选择器 |
| Pose 列表 | `GET /poses` | 姿势选择器 |
| Pose 推荐 | `POST /poses/recommend` | 智能推荐姿势 |
| 综合推荐 | `POST /recommend` | 智能推荐 LoRA + 参考图 |

### 完全缺失 (需新建)

| 功能 | 说明 |
|------|------|
| 用户系统 | 注册、登录、Profile、Token 管理 |
| 积分/会员 | Premium 订阅、积分扣费 |
| 订单系统 | 支付、订单历史 |
| 签到系统 | 每日签到奖励 |
| 图片擦除 | AiEraser 工具 |
