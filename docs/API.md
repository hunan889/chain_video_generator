# API 接口文档

> Base URL: `http://<host>:<port>`
>
> 认证: 无 (公开接口)
>
> 通用响应格式: JSON

---

## 目录

1. [高级工作流 (Workflow)](#1-高级工作流-workflow)
2. [视频生成 (Generate)](#2-视频生成-generate)
3. [视频续写 (Extend)](#3-视频续写-extend)
4. [链式生成 (Chain)](#4-链式生成-chain)
5. [后处理 (PostProcess)](#5-后处理-postprocess)
6. [Prompt 工程](#6-prompt-工程)
7. [任务管理 (Tasks)](#7-任务管理-tasks)
8. [生成历史 (History)](#8-生成历史-history)
9. [LoRA 管理](#9-lora-管理)
10. [Pose 管理](#10-pose-管理)
11. [CivitAI 集成](#11-civitai-集成)
12. [第三方 API 代理](#12-第三方-api-代理)
13. [预设配置 (Presets)](#13-预设配置-presets)
14. [管理接口 (Admin)](#14-管理接口-admin)
15. [健康检查](#15-健康检查)
16. [枚举值参考](#16-枚举值参考)

---

## 1. 高级工作流 (Workflow)

完整的多阶段工作流引擎，支持 T2V、首帧生成、人脸/全身参考等多种模式。

### POST `/api/v1/workflow/generate-advanced`

创建并启动高级工作流。

**Request Body** (`application/json`):

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mode` | string | `"t2v"` | 模式: `t2v`, `first_frame`, `face_reference`, `full_body_reference` |
| `user_prompt` | string | `""` | 用户提示词 (最长 2000 字符) |
| `pose_keys` | string[] | null | 姿势关键字列表 |
| `reference_image` | string | null | 参考图片 (Base64 或 URL) |
| `resolution` | string | null | 分辨率 |
| `aspect_ratio` | string | null | 宽高比 |
| `duration` | int | null | 时长(秒) |
| `first_frame_source` | string | null | 首帧来源 |
| `uploaded_first_frame` | string | null | 上传的首帧 |
| `auto_analyze` | bool | `true` | 自动分析 prompt |
| `auto_lora` | bool | `true` | 自动推荐 LoRA |
| `auto_prompt` | bool | `true` | 自动优化 prompt |
| `t2i_params` | object | null | 文生图参数覆盖 |
| `seedream_params` | object | null | SeeDream 编辑参数 |
| `video_params` | object | null | 视频生成参数覆盖 |
| `internal_config` | object | null | 内部配置覆盖 |
| `turbo` | bool | `true` | 是否使用 turbo (5B) 模型 |
| `mmaudio` | object | null | 音频生成配置 |
| `parent_workflow_id` | string | null | 父工作流 ID (续写场景) |

**Response**:

```json
{
  "workflow_id": "wf_abc123",
  "status": "running",
  "current_stage": "prompt_analysis",
  "stages": [
    {"name": "prompt_analysis", "status": "running", "error": null},
    {"name": "first_frame", "status": "pending", "error": null},
    {"name": "seedream", "status": "pending", "error": null},
    {"name": "video_generation", "status": "pending", "error": null}
  ],
  "chain_id": null,
  "final_video_url": null,
  "first_frame_url": null,
  "edited_frame_url": null,
  "error": null,
  "progress": 0.0,
  "elapsed_time": null,
  "parent_workflow_id": null
}
```

### GET `/api/v1/workflow/status/{workflow_id}`

查询工作流状态。

| 参数 | 位置 | 类型 | 说明 |
|------|------|------|------|
| `workflow_id` | path | string | 工作流 ID |
| `detail` | query | bool | 是否返回详细信息 (含 stage_details, analysis_result 等) |

**Response**: 同上述 Response，当 `detail=true` 时额外包含:

```json
{
  "user_prompt": "...",
  "mode": "t2v",
  "stage_details": { "video_generation": { "model": "...", ... } },
  "created_at": 1712000000,
  "completed_at": 1712000060,
  "analysis_result": { ... }
}
```

### POST `/api/v1/workflow/{workflow_id}/cancel`

取消运行中的工作流。

**Response**: `{"cancelled": true, "workflow_id": "..."}`

### POST `/api/v1/workflow/{workflow_id}/regenerate`

使用相同参数重新生成工作流。

**Response**: 同 `generate-advanced` 的 Response。

### GET `/api/v1/workflow/default-config`

获取指定模式的默认配置。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| `mode` | query | string | 是 | `t2v`, `first_frame`, `face_reference`, `full_body_reference` |
| `turbo` | query | bool | 否 | 默认 `false` |
| `resolution` | query | string | 否 | 默认 `"1080p"` |

### GET `/api/v1/workflow/list`

列出可用的工作流 JSON 模板文件。

**Response**:
```json
{
  "workflows": [
    {"name": "t2v_a14b", "filename": "t2v_a14b.json", "size": 12345}
  ]
}
```

---

## 2. 视频生成 (Generate)

底层视频生成接口，直接提交 ComfyUI 任务。

### POST `/api/v1/generate`

文生视频 (T2V)。支持三种 Content-Type:

**方式一: `application/json`**

```json
{
  "prompt": "a cat walking in the garden",
  "negative_prompt": "",
  "model": "a14b",
  "mode": "t2v",
  "width": 832,
  "height": 480,
  "num_frames": 81,
  "fps": 16,
  "steps": 20,
  "cfg": 6.0,
  "shift": 8.0,
  "seed": -1,
  "loras": [{"name": "style_lora", "strength": 0.8}],
  "auto_lora": false,
  "auto_prompt": false,
  "extract_last_frame": false
}
```

**方式二: `multipart/form-data`** (带文件上传)

| 字段 | 类型 | 说明 |
|------|------|------|
| `params` | string (JSON) | 参数 JSON 字符串 (同上) |
| `image` | file | 可选，输入图片 |
| `face_image` | file | 可选，人脸图片 (换脸用) |

**参数说明**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt` | string | **必填** | 提示词 |
| `negative_prompt` | string | `""` | 负面提示词 |
| `model` | string | `"a14b"` | 模型: `a14b`, `5b` |
| `mode` | string | `"t2v"` | 模式，见[枚举值](#16-枚举值参考) |
| `width` | int | `832` | 宽度 |
| `height` | int | `480` | 高度 |
| `num_frames` | int | `81` | 帧数 |
| `fps` | int | `16` | 帧率 |
| `steps` | int | `20` | 采样步数 |
| `cfg` | float | `6.0` | CFG Scale |
| `shift` | float | `8.0` | Shift 值 |
| `seed` | int | `-1` | 随机种子 (-1 为随机) |
| `scheduler` | string | `""` | 调度器 |
| `model_preset` | string | `""` | 模型预设 |
| `t5_preset` | string | `""` | T5 预设 |
| `loras` | array | `[]` | LoRA 列表 `[{"name": "...", "strength": 0.8}]` |
| `upscale` | bool | `false` | 是否放大 |
| `auto_lora` | bool | `false` | 自动推荐 LoRA |
| `auto_prompt` | bool | `false` | 自动优化 prompt |
| `face_swap` | object | null | 换脸配置 |
| `extract_last_frame` | bool | `false` | 是否提取最后一帧 |
| `workflow_json` | string | null | 预构建的工作流 JSON |

**Response**:
```json
{"task_id": "abc123", "status": "queued"}
```

### POST `/api/v1/generate/i2v`

图生视频 (I2V)。仅支持 `multipart/form-data`。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `image` | file | **是** | 输入图片 |
| `params` | string (JSON) | 否 | 参数 JSON，额外支持以下 I2V 专属参数 |
| `face_image` | file | 否 | 人脸图片 |

**I2V 额外参数**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `noise_aug_strength` | float | null | 噪声增强强度 |
| `motion_amplitude` | float | null | 运动幅度 |
| `color_match` | bool | null | 颜色匹配 |
| `color_match_method` | string | null | 颜色匹配方法 |
| `resize_mode` | string | null | 缩放模式 |

**Response**: `{"task_id": "abc123", "status": "queued"}`

---

## 3. 视频续写 (Extend)

### POST `/api/v1/generate/extend`

基于已完成任务的最后一帧生成续写视频。

**Request Body** (`application/json`):

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `parent_task_id` | string | - | **是** | 父任务 ID |
| `prompt` | string | `""` | 否 | 续写提示词 |
| `negative_prompt` | string | `""` | 否 | 负面提示词 |
| `num_frames` | int | `81` | 否 | 帧数 |
| `steps` | int | `20` | 否 | 采样步数 |
| `cfg` | float | `6.0` | 否 | CFG Scale |
| `seed` | int | `-1` | 否 | 随机种子 |
| `loras` | array | `[]` | 否 | LoRA 列表 |

**Response**: `{"task_id": "abc123", "status": "queued"}`

---

## 4. 链式生成 (Chain)

多段连续视频生成，自动提取上一段最后一帧作为下一段首帧。

### POST `/api/v1/chains`

创建链式生成任务。

**Request Body**:

```json
{
  "segments": [
    {
      "prompt": "a girl walks into a forest",
      "duration": 5.0,
      "workflow": null,
      "image_filename": null,
      "extract_last_frame": true
    },
    {
      "prompt": "she discovers a hidden waterfall",
      "duration": 5.0
    }
  ],
  "model": "a14b",
  "auto_continue": false
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `segments` | ChainSegment[] | **必填** | 片段列表 (至少 1 个) |
| `model` | string | `"a14b"` | 模型: `a14b`, `5b` |
| `auto_continue` | bool | `false` | 自动续写 |

**ChainSegment**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt` | string | - | 提示词 |
| `duration` | float | `5.0` | 时长(秒) |
| `workflow` | string | null | 工作流模板名 |
| `image_filename` | string | null | 首帧图片文件名 |
| `extract_last_frame` | bool | - | 是否提取最后帧 |

**Response**:
```json
{
  "chain_id": "chain_abc123",
  "status": "running",
  "segments": [...]
}
```

### GET `/api/v1/chains/{chain_id}`

查询链式任务状态。

### GET `/api/v1/chains`

列出所有链式任务。

### POST `/api/v1/chains/{chain_id}/cancel`

取消链式任务。

**Response**: `{"status": "cancelled"}`

---

## 5. 后处理 (PostProcess)

所有后处理接口均为 `multipart/form-data`。

### POST `/api/v1/postprocess/interpolate`

帧插值 (RIFE)，提高视频流畅度。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `task_id` | string | **必填** | 源任务 ID |
| `multiplier` | int | `2` | 倍率 |
| `fps` | float | `16.0` | 输出帧率 |

### POST `/api/v1/postprocess/upscale`

视频放大。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `task_id` | string | **必填** | 源任务 ID |
| `model` | string | `"4x_foolhardy_Remacri"` | 放大模型 |
| `resize_to` | string | `"2x"` | 目标尺寸 |

### POST `/api/v1/postprocess/audio`

AI 音频生成 (MMAudio)。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `task_id` | string | **必填** | 源任务 ID |
| `prompt` | string | **必填** | 音频描述 |
| `negative_prompt` | string | `""` | 负面描述 |
| `steps` | int | `25` | 步数 |
| `cfg` | float | `4.5` | CFG |
| `fps` | float | `16.0` | FPS |

### POST `/api/v1/postprocess/faceswap`

人脸替换 (ReActor)。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `task_id` | string | **必填** | 源任务 ID |
| `strength` | float | `1.0` | 替换强度 |
| `face_image` | file | **必填** | 人脸图片 |

**统一 Response**: `{"task_id": "abc123", "status": "queued"}`

---

## 6. Prompt 工程

### POST `/api/v1/prompt/optimize`

优化用户提示词。

**Request Body**:

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `prompt` | string | - | **是** | 原始提示词 (1-2000 字符) |
| `lora_names` | string[] | `[]` | 否 | 当前使用的 LoRA 名称 |
| `mode` | string | `"i2v"` | 否 | `t2v` 或 `i2v` |
| `image_base64` | string | null | 否 | 图片 Base64 (I2V 模式) |
| `duration` | float | `3.3` | 否 | 视频时长 (0.5-10) |

**Response**:
```json
{
  "original_prompt": "a cat",
  "optimized_prompt": "A fluffy orange cat walks gracefully...",
  "trigger_words_used": ["style_trigger"],
  "explanation": "Added motion details and style keywords..."
}
```

### POST `/api/v1/prompt/describe-image`

图片描述生成。

**Request Body**: `{"image_base64": "<base64 string>"}`

**Response**: `{"description": "A young woman standing in a garden..."}`

### POST `/api/v1/prompt/continuation`

生成续写 prompt。

**Request Body**:

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `user_intent` | string | - | **是** | 用户意图 |
| `previous_video_prompt` | string | - | **是** | 前一段视频 prompt |
| `frame_image_base64` | string | null | 否 | 最后一帧 Base64 |
| `duration` | float | `3.0` | 否 | 目标时长 |
| `continuation_index` | int | `1` | 否 | 续写索引 |

**Response**: `{"continuation_prompt": "..."}`

---

## 7. 任务管理 (Tasks)

### GET `/api/v1/tasks/{task_id}`

查询单个任务状态。

**Response**:
```json
{
  "task_id": "abc123",
  "status": "completed",
  "progress": 1.0,
  "video_url": "https://cdn.example.com/output.mp4",
  "error": null,
  "params": { ... },
  "created_at": "...",
  "completed_at": "..."
}
```

### POST `/api/v1/tasks/{task_id}/cancel`

取消排队中的任务。

**Response**: `{"cancelled": true, "task_id": "abc123"}`

---

## 8. 生成历史 (History)

以下三个路径为同一接口的别名:
- `GET /api/v1/tasks`
- `GET /api/v1/generation/history`
- `GET /api/v1/workflow/history`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `page` | int | `1` | 页码 |
| `page_size` | int | `24` | 每页数量 (最大 100) |
| `status` | string | null | 按状态过滤 |
| `category` | string | null | 按分类过滤: `local`, `thirdparty`, `postprocess`, `utility` |
| `q` | string | null | 搜索关键词 |

**Response**:
```json
{
  "workflows": [ ... ],
  "total": 150,
  "total_pages": 7,
  "page": 1,
  "page_size": 24,
  "category_counts": {
    "local": 100,
    "thirdparty": 30,
    "postprocess": 15,
    "utility": 5
  }
}
```

### POST `/api/v1/workflow/{workflow_id}/regenerate`

从历史记录重新生成。

### GET `/api/v1/workflow/status/{workflow_id}`

查询工作流状态 (同[高级工作流](#get-apiv1workflowstatusworkflow_id))。

---

## 9. LoRA 管理

### GET `/api/v1/loras`

获取所有可用 LoRA 列表。

**Response**:
```json
{
  "loras": [
    {
      "name": "style_lora",
      "filename": "style_lora.safetensors",
      "trigger_words": ["style_trigger"],
      ...
    }
  ]
}
```

### POST `/api/v1/loras/recommend`

基于 prompt 推荐 LoRA。

**Request Body**: `{"prompt": "anime girl dancing"}`

**Response**:
```json
{
  "loras": [
    {"name": "anime_style", "strength": 0.8}
  ]
}
```

### POST `/api/v1/loras/download`

从 CivitAI 下载 LoRA。

**Request Body**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `civitai_version_id` | int | CivitAI 版本 ID |
| `filename` | string | 保存文件名 |

**Response**: `{"task_id": "abc123", "status": "queued"}`

---

## 10. Pose 管理

### GET `/api/v1/poses`

获取 Pose 列表。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `include_disabled` | bool | `false` | 是否包含禁用的 |
| `category` | string | null | 按分类过滤 |

**Response**: `{"poses": [...]}`

### GET `/api/v1/poses/{pose_id}/config`

获取 Pose 完整配置 (含参考图、LoRA、prompt 模板)。

**Response**:
```json
{
  "pose": { ... },
  "reference_images": [ ... ],
  "image_loras": [ ... ],
  "video_loras": [ ... ],
  "prompt_templates": [ ... ]
}
```

### POST `/api/v1/poses/recommend`

根据 prompt 推荐 Pose。

**Request Body**:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt` | string | **必填** | 提示词 |
| `top_k` | int | `5` | 返回数量 |

### POST `/api/v1/poses/match`

同 `/recommend`，别名接口。

### POST `/api/v1/poses/batch-config`

批量获取 Pose 配置。

**Request Body**: `{"pose_ids": [1, 2, 3]}` 或 `[1, 2, 3]`

**Response**: `{"1": {...}, "2": {...}, "3": {...}}`

---

## 11. CivitAI 集成

### GET `/api/v1/civitai/search`

搜索 CivitAI 模型。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | string | - | 搜索关键词 |
| `limit` | int | `20` | 数量 (1-100) |
| `cursor` | string | null | 分页游标 |
| `nsfw` | bool | `true` | 是否包含 NSFW |
| `sort` | string | null | 排序方式 |
| `base_model` | string | null | 基础模型过滤 |

### GET `/api/v1/civitai/models/{model_id}`

获取 CivitAI 模型详情。

### POST `/api/v1/civitai/download`

下载 CivitAI 模型。

**Request Body**: `{"civitai_version_id": 12345, "filename": "model.safetensors"}`

**Response**: `{"task_id": "abc123", "status": "queued"}`

---

## 12. 第三方 API 代理

### Wan2.6 (阿里通义万相)

#### POST `/api/v1/thirdparty/wan26/text-to-video`

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `prompt` | string | - | **是** | 提示词 |
| `model` | string | `"wan2.6-t2v"` | 否 | 模型 |
| `negative_prompt` | string | null | 否 | 负面提示词 |
| `duration` | int | `5` | 否 | 时长(秒) |
| `size` | string | `"1280*720"` | 否 | 尺寸 |
| `shot_type` | string | null | 否 | 镜头类型 |
| `prompt_extend` | bool | `true` | 否 | 自动扩展 prompt |
| `audio_url` | string | null | 否 | 音频 URL |
| `seed` | int | null | 否 | 随机种子 |

#### POST `/api/v1/thirdparty/wan26/image-to-video`

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `image` | string | - | **是** | 图片 URL 或 Base64 |
| `model` | string | `"wan2.6-i2v"` | 否 | 模型 |
| `prompt` | string | null | 否 | 提示词 |
| `negative_prompt` | string | null | 否 | 负面提示词 |
| `duration` | int | `5` | 否 | 时长(秒) |
| `resolution` | string | `"720P"` | 否 | 分辨率 |
| `shot_type` | string | null | 否 | 镜头类型 |
| `prompt_extend` | bool | `true` | 否 | 自动扩展 prompt |
| `audio_url` | string | null | 否 | 音频 URL |
| `seed` | int | null | 否 | 随机种子 |

#### GET `/api/v1/thirdparty/wan26/tasks/{task_id}`

查询 Wan2.6 任务状态。

### Seedance (字节豆包)

#### POST `/api/v1/thirdparty/seedance/text-to-video`

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `prompt` | string | - | **是** | 提示词 |
| `model` | string | `"seedance-1-5-pronew"` | 否 | 模型 |
| `duration` | int | `5` | 否 | 时长 |
| `resolution` | string | `"720P"` | 否 | 分辨率 |

#### POST `/api/v1/thirdparty/seedance/image-to-video`

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `image` | string | - | **是** | 图片 URL 或 Base64 |
| `prompt` | string | null | 否 | 提示词 |
| `model` | string | `"seedance-1-0-pro-250528"` | 否 | 模型 |
| `duration` | int | `5` | 否 | 时长 |
| `resolution` | string | `"720p"` | 否 | 分辨率 |

#### GET `/api/v1/thirdparty/seedance/tasks/{task_id}`

查询 Seedance 任务状态。

**第三方提交统一 Response**:
```json
{
  "success": true,
  "task_id": "tp_abc123",
  "task_status": "submitted",
  "provider": "wan26",
  "error": null
}
```

**第三方查询统一 Response**:
```json
{
  "success": true,
  "task_id": "tp_abc123",
  "task_status": "completed",
  "video_url": "https://...",
  "error_message": null,
  "provider": "wan26"
}
```

---

## 13. 预设配置 (Presets)

### GET `/api/v1/model-presets`

获取模型参数预设。

### GET `/api/v1/t5-presets`

获取 T5 编码器预设。

---

## 14. 管理接口 (Admin)

### Worker 管理

#### GET `/api/v1/admin/workers`

获取 GPU Worker 信息。

**Response**: `{"workers": [...], "queue_lengths": {"a14b": 2, "5b": 0}}`

#### GET `/api/v1/admin/gpu-status`

获取 GPU 状态。

**Response**: `{"gpus": [...], "queue_lengths": {...}}`

### 设置管理

#### GET `/api/v1/admin/settings`

获取全局设置。

#### PUT `/api/v1/admin/settings`

更新全局设置。

**默认设置**:
```json
{
  "prompt_optimize_min_chars": 20,
  "prompt_optimize_non_turbo": true,
  "inject_trigger_prompt": true,
  "inject_trigger_words": true
}
```

### Pose 管理 (Admin)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/admin/poses` | 获取所有 Pose (含禁用) |
| POST | `/api/v1/admin/poses` | 创建 Pose |
| PUT | `/api/v1/admin/poses/{pose_id}` | 更新 Pose |
| DELETE | `/api/v1/admin/poses/{pose_id}` | 删除 Pose |
| POST | `/api/v1/admin/poses/reference-images` | 添加参考图 |
| DELETE | `/api/v1/admin/poses/reference-images/{image_id}` | 删除参考图 |
| POST | `/api/v1/admin/poses/loras` | 添加 LoRA 关联 |
| DELETE | `/api/v1/admin/poses/loras/{lora_id}` | 删除 LoRA 关联 |
| PATCH | `/api/v1/admin/poses/loras/{lora_id}` | 更新 LoRA 属性 |
| POST | `/api/v1/admin/poses/{pose_id}/auto-associate` | 自动关联 LoRA |

### Pose 同义词管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/admin/pose-synonyms` | 获取所有同义词映射 |
| PUT | `/api/v1/admin/pose-synonyms/{pose_key}` | 更新 Pose 同义词 |

---

## 15. 健康检查

### GET `/health`

**Response**:
```json
{
  "status": "ok",
  "redis": true,
  "workers": 2
}
```

`status` 为 `"ok"` 或 `"degraded"`。

---

## 16. 枚举值参考

### ModelType (模型)

| 值 | 说明 |
|----|------|
| `a14b` | Wan2.2 14B (多 GPU, 两阶段 HIGH→LOW) |
| `5b` | Wan2.2 5B (单 GPU, turbo 模式) |

### GenerateMode (生成模式)

| 值 | 分类 | 说明 |
|----|------|------|
| `t2v` | local | 文生视频 |
| `i2v` | local | 图生视频 |
| `extend` | local | 视频续写 |
| `vace_ref2v` | local | VACE 参考生成 |
| `vace_v2v` | local | VACE 视频转视频 |
| `vace_inpainting` | local | VACE 修复 |
| `vace_flf2v` | local | VACE 首末帧生成 |
| `faceswap` | local | 人脸替换 |
| `concat` | postprocess | FFmpeg 视频拼接 |
| `interpolate` | postprocess | RIFE 帧插值 |
| `upscale` | postprocess | 视频放大 |
| `audio` | postprocess | MMAudio 音频生成 |
| `lora_download` | utility | LoRA 下载 |
| `wan26_t2v` | thirdparty | 通义万相 T2V |
| `wan26_i2v` | thirdparty | 通义万相 I2V |
| `seedance_t2v` | thirdparty | 字节豆包 T2V |
| `seedance_i2v` | thirdparty | 字节豆包 I2V |

### TaskStatus (任务状态)

| 值 | 说明 |
|----|------|
| `queued` | 排队中 |
| `running` | 运行中 |
| `completed` | 已完成 |
| `failed` | 失败 |

### TaskCategory (任务分类)

| 值 | 说明 |
|----|------|
| `local` | 本地 ComfyUI GPU 执行 |
| `thirdparty` | 第三方 API 委托 |
| `postprocess` | 后处理 |
| `utility` | 工具类 |

---

## 反向代理

以下路径会被转发到 monolith 服务:

- `/api/v1/admin/loras/*`
- `/api/v1/admin/embeddings/*`
- `/api/v1/pose-images/*`
- `/api/v1/upload/*`
- `/api/v1/results/*`
- `/api/v1/proxy-media/*`

其他未匹配路径返回 404。
