# 高级工作流 API 文档

> Base URL: `http://<host>:8000/api/v1`
>
> 所有接口均需要 API Key 认证，通过 Header `X-API-Key` 或查询参数 `api_key` 传递。

---

## 目录

1. [生成视频（一键工作流）](#1-生成视频一键工作流)
2. [查询工作流状态](#2-查询工作流状态)

---

## 1. 生成视频（一键工作流）

一键式高级视频生成，自动编排 Prompt 分析 → 首帧获取 → SeeDream 编辑 → 视频生成 全流程。

### 请求

```
POST /workflow/generate-advanced
Content-Type: application/json
```

### 请求参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `mode` | string | **是** | - | 工作流模式：`face_reference`（人脸参考）、`full_body_reference`（全身参考）、`first_frame`（首帧模式） |
| `user_prompt` | string | **是** | - | 用户描述提示词，1-2000 字符 |
| `reference_image` | string | 否 | null | 参考图片（base64、URL 或路径），`face_reference` / `full_body_reference` 模式下使用 |
| `uploaded_first_frame` | string | 否 | null | 上传的首帧图片（`first_frame` 模式下使用，base64 或 URL） |
| `pose_keys` | string[] | 否 | null | 选定的姿势 key 列表 |
| `resolution` | string | 否 | null | 分辨率：`480p`、`720p`、`1080p` |
| `aspect_ratio` | string | 否 | null | 宽高比：`16:9`、`3:4` 等 |
| `duration` | int | 否 | null | 视频时长（秒） |
| `first_frame_source` | string | 否 | null | 首帧来源：`generate`（T2I 生成）、`select_existing`（使用已有图片） |
| `turbo` | bool | 否 | false | Turbo 模式，更快但质量略低（详见 [Turbo 模式差异](#turbo-模式差异)） |
| `mmaudio` | object | 否 | null | MMAudio 音频生成配置（详见 [MMAudio 参数](#mmaudio-参数)） |
| `parent_workflow_id` | string | 否 | null | 父工作流 ID，用于 Story 续写模式。设置后跳过 Stage 2-3，使用父视频最后帧作为运动参考 |
| `auto_analyze` | bool | 否 | true | 自动分析并推荐 LoRA |
| `auto_lora` | bool | 否 | true | 自动选择 LoRA |
| `auto_prompt` | bool | 否 | true | 自动优化 Prompt |
| `seedream_params` | object | 否 | 见下方 | SeeDream 编辑参数 |

#### MMAudio 参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `enabled` | bool | **是** | - | 是否启用音频生成 |
| `prompt` | string | 否 | "" | 音频描述提示词 |
| `negative_prompt` | string | 否 | "" | 音频负面提示词 |
| `steps` | int | 否 | 25 | 音频生成步数 |
| `cfg` | float | 否 | 4.5 | 音频 CFG 引导系数 |

#### seedream_params 默认值

```json
{
  "edit_mode": "face_wearings",
  "enable_reactor_first": true,
  "strength": 0.8
}
```

#### 图片字段支持的格式

| 格式 | 示例 |
|------|------|
| Base64 | `data:image/jpeg;base64,/9j/4AAQ...` |
| HTTP/HTTPS URL | `https://example.com/image.jpg` |
| API 结果路径 | `/api/v1/results/abc123.png` |
| 上传路径 | `/uploads/abc123.png` |
| 姿势文件路径 | `/pose-files/pose/abc123.jpg` |
| 本地文件名 | `abc123.png` |

### 请求示例

**基本用法（默认参数）：**

```json
{
  "mode": "face_reference",
  "user_prompt": "一个女孩在海边跳舞，微风吹过长发",
  "reference_image": "https://example.com/face.jpg",
  "resolution": "480p",
  "aspect_ratio": "3:4",
  "duration": 5,
  "turbo": false
}
```

**带 MMAudio 音频生成：**

```json
{
  "mode": "face_reference",
  "user_prompt": "一个女孩在海边跳舞",
  "reference_image": "https://example.com/face.jpg",
  "resolution": "480p",
  "aspect_ratio": "3:4",
  "duration": 5,
  "turbo": false,
  "mmaudio": {
    "enabled": true,
    "prompt": "ocean waves, wind blowing",
    "steps": 25,
    "cfg": 4.5
  }
}
```

**Story 续写模式：**

```json
{
  "mode": "face_reference",
  "user_prompt": "她转身走向大海",
  "parent_workflow_id": "wf_abc123",
  "duration": 5
}
```

### 响应

```json
{
  "workflow_id": "wf_abc123",
  "status": "running",
  "current_stage": "prompt_analysis",
  "stages": [
    {"name": "prompt_analysis", "status": "running", "sub_stage": null, "error": null},
    {"name": "first_frame_acquisition", "status": "pending", "sub_stage": null, "error": null},
    {"name": "seedream_edit", "status": "pending", "sub_stage": null, "error": null},
    {"name": "video_generation", "status": "pending", "sub_stage": null, "error": null}
  ],
  "chain_id": null,
  "final_video_url": null,
  "first_frame_url": null,
  "edited_frame_url": null,
  "estimated_total_time": 120,
  "error": null,
  "progress": 0.01,
  "video_progress": null,
  "current_step": null,
  "max_step": null,
  "total_steps": null,
  "completed_steps": null,
  "elapsed_time": 0.5
}
```

### 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `workflow_id` | string | 工作流唯一 ID，用于轮询查询状态 |
| `status` | string | 状态：`queued`、`running`、`completed`、`failed` |
| `current_stage` | string | 当前执行阶段名 |
| `stages` | WorkflowStage[] | 所有阶段的状态列表（见下方） |
| `chain_id` | string? | 关联的视频生成链式任务 ID |
| `final_video_url` | string? | 最终视频 URL（完成后可用） |
| `first_frame_url` | string? | 首帧图片 URL |
| `edited_frame_url` | string? | SeeDream 编辑后的图片 URL |
| `estimated_total_time` | int? | 预估总耗时（秒） |
| `error` | string? | 错误信息（失败时返回） |
| `progress` | float? | 整体进度 0.0-1.0 |
| `video_progress` | float? | 视频生成阶段进度 0.0-1.0 |
| `current_step` | int? | 当前步骤数 |
| `max_step` | int? | 当前阶段总步骤数 |
| `total_steps` | int? | 全部阶段总步骤数 |
| `completed_steps` | int? | 已完成步骤数 |
| `elapsed_time` | float? | 已耗时（秒） |

**WorkflowStage 结构：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 阶段名 |
| `status` | string | `pending`、`running`、`completed`、`failed` |
| `sub_stage` | string? | 子阶段信息（JSON 字符串，包含该阶段的详细参数和结果） |
| `error` | string? | 该阶段的错误信息 |

**工作流阶段及权重：**

| 阶段名 | 权重 | 说明 |
|--------|------|------|
| `prompt_analysis` | 2% | Prompt 分析、LoRA 推荐、Prompt 优化 |
| `first_frame_acquisition` | 8% | 首帧获取（T2I 生成或选择已有姿势图） |
| `seedream_edit` | 5% | SeeDream 图片编辑（换脸/换装/换配饰） |
| `video_generation` | 85% | 视频生成（含后处理：超分、插帧、音频） |

---

## 2. 查询工作流状态

轮询查询工作流执行进度和结果。

### 请求

```
GET /workflow/status/{workflow_id}
```

### 路径参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `workflow_id` | string | 工作流 ID（由 `generate-advanced` 接口返回） |

### 响应格式

与 [生成视频](#1-生成视频一键工作流) 接口返回格式完全一致（`WorkflowGenerateResponse`）。

### 响应示例（进行中）

```json
{
  "workflow_id": "wf_abc123",
  "status": "running",
  "current_stage": "video_generation",
  "stages": [
    {"name": "prompt_analysis", "status": "completed", "sub_stage": null, "error": null},
    {"name": "first_frame_acquisition", "status": "completed", "sub_stage": null, "error": null},
    {"name": "seedream_edit", "status": "completed", "sub_stage": null, "error": null},
    {"name": "video_generation", "status": "running", "sub_stage": "stage_high", "error": null}
  ],
  "first_frame_url": "/api/v1/results/frame_abc123.png",
  "edited_frame_url": "/api/v1/results/edited_abc123.png",
  "progress": 0.45,
  "video_progress": 0.47,
  "current_step": 10,
  "max_step": 20,
  "elapsed_time": 55.3
}
```

### 响应示例（已完成）

```json
{
  "workflow_id": "wf_abc123",
  "status": "completed",
  "current_stage": "video_generation",
  "stages": [
    {"name": "prompt_analysis", "status": "completed", "sub_stage": null, "error": null},
    {"name": "first_frame_acquisition", "status": "completed", "sub_stage": null, "error": null},
    {"name": "seedream_edit", "status": "completed", "sub_stage": null, "error": null},
    {"name": "video_generation", "status": "completed", "sub_stage": null, "error": null}
  ],
  "final_video_url": "/api/v1/results/video_abc123.mp4",
  "first_frame_url": "/api/v1/results/frame_abc123.png",
  "edited_frame_url": "/api/v1/results/edited_abc123.png",
  "progress": 1.0,
  "video_progress": 1.0
}
```

### 响应示例（失败）

```json
{
  "workflow_id": "wf_abc123",
  "status": "failed",
  "current_stage": "video_generation",
  "stages": [
    {"name": "prompt_analysis", "status": "completed", "sub_stage": null, "error": null},
    {"name": "first_frame_acquisition", "status": "completed", "sub_stage": null, "error": null},
    {"name": "seedream_edit", "status": "completed", "sub_stage": null, "error": null},
    {"name": "video_generation", "status": "failed", "sub_stage": null, "error": "ComfyUI timeout"}
  ],
  "error": "ComfyUI timeout",
  "progress": 0.15
}
```

### 推荐轮询策略

- 轮询间隔：每 **2-3 秒** 一次
- 终止条件：`status` 为 `completed` 或 `failed` 时停止轮询
- 前端可根据 `progress` 字段展示进度条

---

## 附录

### 认证方式

```bash
# Header 方式（推荐）
curl -H "X-API-Key: your-api-key" http://host:8000/api/v1/workflow/status/wf_abc123

# 查询参数方式
curl http://host:8000/api/v1/workflow/status/wf_abc123?api_key=your-api-key
```

### 错误响应格式

```json
{
  "detail": "错误描述信息"
}
```

| 状态码 | 说明 |
|--------|------|
| `400` | 请求参数错误 |
| `401` | 认证失败（API Key 无效） |
| `404` | 工作流不存在 |
| `503` | ComfyUI 实例不可用 |

### Turbo 模式差异

| 配置项 | 普通模式 | Turbo 模式 |
|--------|----------|------------|
| Prompt 优化 | LLM 优化 | 跳过 |
| LoRA 重排序 | LLM 重排序 | 跳过 |
| Steps | 5 | 5 |
| CFG | 2.0 | 1.0 |
| SeeDream（face_reference） | 执行 | 跳过 |
| SeeDream（full_body_reference） | 执行 | 执行 |
| 超分倍数 | 1.5x | 2.0x |
| 帧插值 | 启用（2x） | 禁用 |
| MMAudio | 禁用（可覆盖） | 禁用（可覆盖） |

