# H5 对接 API 文档

> 面向 GiMe.AI H5 前端的完整 API 接口说明。

**Base URL**: 由环境变量 `VITE_API_BASE_URL` 配置
**认证**: 所有接口需携带 `X-API-Key` Header

---

## 目录

1. [图片变换 API](#1-图片变换-api)
2. [视频变换 API](#2-视频变换-api)
3. [AI 视频生成 API](#3-ai-视频生成-api)
4. [文件上传 API](#4-文件上传-api)
5. [任务轮询 API](#5-任务轮询-api)
6. [通用说明](#6-通用说明)

---

## 1. 图片变换 API

### `POST /api/v1/image/transform`

统一图片变换接口，通过 `scene` 参数选择处理场景。

**Content-Type**: `multipart/form-data`

#### 通用请求参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `scene` | string | ✅ | — | 场景模板，见下方场景列表 |
| `image` | File | ✅ | — | 主图 |
| `reference` | File | 部分场景必填 | — | 参考图 |
| `prompt` | string | 部分场景必填 | 场景默认值 | 文字提示词 |
| `size` | string | ❌ | `"adaptive"` | 输出尺寸，如 `"1024x1024"` |
| `seed` | int | ❌ | — | 随机种子，用于结果复现 |
| `advanced` | bool | ❌ | `false` | 启用高级处理模式 |
| `options` | JSON string | ❌ | `{}` | 场景专属参数，详见各场景说明 |

#### 通用响应

```json
{
  "url": "https://example.com/result.jpg",
  "scene": "clothes",
  "size": "1024x1024",
  "seed": 42
}
```

---

### 场景列表

#### 1.1 `face_swap` — 换脸

将主图人脸融合到参考图场景中。

| 参数 | 要求 | 说明 |
|------|------|------|
| `image` | ✅ 必填 | 人脸源图 |
| `reference` | ✅ 必填 | 目标场景图 |
| `prompt` | ❌ | — |
| `advanced` | 支持 | `false`=快速换脸，`true`=高质量精修 |

`options` 可选参数：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `expression_keep` | float | `0.0` | 保留原始表情程度，范围 0.0~1.0 |
| `preserve_occlusion` | bool | `false` | 保留面部遮挡物（眼镜、口罩等） |

**请求示例**：

```
POST /api/v1/image/transform
Content-Type: multipart/form-data

scene=face_swap
image=@face.jpg
reference=@target_scene.jpg
advanced=true
options={"expression_keep": 0.3}
```

---

#### 1.2 `pose` — 姿势换脸

将主图人脸换到参考图的姿势人物上。与 `face_swap` 基础模式行为一致，不支持 `advanced`。

| 参数 | 要求 | 说明 |
|------|------|------|
| `image` | ✅ 必填 | 人脸源图 |
| `reference` | ✅ 必填 | 目标姿势图 |
| `prompt` | ❌ | — |

---

#### 1.3 `clothes` — 换衣服

保持主图人物不变，换上参考图中的衣服。

| 参数 | 要求 | 说明 |
|------|------|------|
| `image` | ✅ 必填 | 人物照片 |
| `reference` | ✅ 必填 | 衣服参考图 |
| `prompt` | ❌ 可选 | 补充描述，如 `"casual summer style"` |

---

#### 1.4 `shoot` — 写真

将主图人物融入参考图的场景或风格中。

| 参数 | 要求 | 说明 |
|------|------|------|
| `image` | ✅ 必填 | 人物照片 |
| `reference` | ✅ 必填 | 场景/风格参考图 |
| `prompt` | ❌ 可选 | 补充描述 |

---

#### 1.5 `puzzle` — 创意合成

将主图与参考图进行创意拼合。

| 参数 | 要求 | 说明 |
|------|------|------|
| `image` | ✅ 必填 | 主图 |
| `reference` | ✅ 必填 | 参考图 |
| `prompt` | ❌ 可选 | 补充描述 |

---

#### 1.6 `photo_edit` — 图片编辑

根据文字指令编辑图片。

| 参数 | 要求 | 说明 |
|------|------|------|
| `image` | ✅ 必填 | 待编辑图片 |
| `prompt` | ✅ **必填** | 编辑指令，如 `"change background to beach"` |
| `reference` | ❌ | — |

---

#### 1.7 `eraser` — 擦除

对图片进行擦除处理，返回处理后的完整图片。前端根据用户画笔选区做 mask 合成展示。

| 参数 | 要求 | 说明 |
|------|------|------|
| `image` | ✅ 必填 | 原始图片 |
| `prompt` | ❌ 可选 | — |
| `reference` | ❌ | — |

> 接口返回处理后的全图。前端将用户 brush 选区作为 mask，将结果图与原图进行合成后展示。

---

### 场景参数总览

| 场景 | `image` | `reference` | `prompt` | `advanced` |
|------|---------|-------------|----------|------------|
| `face_swap` | ✅ | ✅ | — | ✅ |
| `pose` | ✅ | ✅ | — | — |
| `clothes` | ✅ | ✅ | 可选 | — |
| `shoot` | ✅ | ✅ | 可选 | — |
| `puzzle` | ✅ | ✅ | 可选 | — |
| `photo_edit` | ✅ | — | ✅ 必填 | — |
| `eraser` | ✅ | — | 可选 | — |

---

## 2. 视频变换 API

### `POST /api/v1/video/transform`

视频变换接口，异步处理，返回 `task_id` 用于轮询。

**Content-Type**: `multipart/form-data`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `scene` | string | ✅ | 目前支持：`face_swap` |
| `video` | File | ✅ | 输入视频（最大 500MB） |
| `reference` | File | ✅ | 参考人脸图 |
| `faces_index` | string | ❌ | 目标面部索引，默认 `"0"`，多脸用逗号分隔如 `"0,1"` |

#### 响应

```json
{
  "task_id": "abc123",
  "status": "queued",
  "scene": "face_swap"
}
```

结果通过 `GET /api/v1/tasks/{task_id}` 轮询获取。

---

## 3. AI 视频生成 API

### `POST /api/v1/workflow/generate-advanced`

多模式 AI 视频生成，支持文生视频、图生视频、人脸/全身参考等。

**Content-Type**: `application/json`

#### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `mode` | string | ✅ | `"t2v"` / `"first_frame"` / `"face_reference"` / `"full_body_reference"` |
| `user_prompt` | string | 条件必填 | 生成提示词（续写时可选） |
| `reference_image` | string | 条件必填 | 参考图 base64 或 URL（face/body 模式必填） |
| `uploaded_first_frame` | string | 条件必填 | 首帧图 base64（first_frame 模式必填） |
| `resolution` | string | ❌ | `"480p"` / `"720p"` / `"1080p"`，默认 `"480p"` |
| `aspect_ratio` | string | ❌ | `"auto"` / `"3:4"` / `"9:16"` / `"16:9"` |
| `duration` | int | ❌ | 时长（秒）：3 / 5 / 10 / 15 |
| `auto_analyze` | bool | ❌ | 自动分析推荐 LoRA，默认 `true` |
| `auto_lora` | bool | ❌ | 自动选择 LoRA，默认 `true` |
| `auto_prompt` | bool | ❌ | 自动优化提示词，默认 `true` |
| `turbo` | bool | ❌ | 加速模式，默认 `false` |
| `mmaudio` | bool | ❌ | 生成配音，默认 `false` |
| `parent_workflow_id` | string | ❌ | 续写来源 workflow ID |

#### 响应

```json
{
  "workflow_id": "wf_abc123",
  "status": "processing"
}
```

#### 工作流阶段

生成过程分阶段执行，可通过轮询获取当前阶段：

1. `prompt_analysis` — 提示词分析与 LoRA 推荐
2. `first_frame_acquisition` — 首帧获取（生成/上传/选择）
3. `seedream_edit` — 首帧编辑（face/body 模式）
4. `video_generation` — 视频生成

### `GET /api/v1/workflow/status/{workflowId}`

轮询工作流状态。

#### 响应

```json
{
  "workflow_id": "wf_abc123",
  "status": "completed",
  "stage": "video_generation",
  "progress": 100,
  "video_url": "https://example.com/result.mp4",
  "first_frame_url": "https://example.com/frame.jpg"
}
```

`status` 取值：`processing` / `completed` / `failed`

---

### 视频续写

通过 `parent_workflow_id` 指定续写来源，从前一个视频的最后一帧继续生成。

```json
{
  "mode": "first_frame",
  "parent_workflow_id": "wf_abc123",
  "user_prompt": "the character starts dancing"
}
```

---

## 4. 文件上传 API

### `POST /api/v1/upload`

上传图片文件，返回可访问的 URL。

**Content-Type**: `multipart/form-data`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | File | ✅ | 图片文件 |

#### 响应

```json
{
  "url": "/api/v1/results/abc123.jpg",
  "filename": "abc123.jpg"
}
```

---

## 5. 任务轮询 API

### `GET /api/v1/tasks/{taskId}`

查询异步任务状态（视频换脸等）。

#### 响应

```json
{
  "task_id": "abc123",
  "status": "completed",
  "progress": 100,
  "video_url": "/api/v1/results/output.mp4"
}
```

`status` 取值：`queued` / `processing` / `completed` / `failed`

---

## 6. 通用说明

### 错误响应

所有接口使用统一错误格式：

```json
{
  "detail": "错误描述信息"
}
```

| HTTP 状态码 | 说明 |
|-------------|------|
| 400 | 参数错误（缺少必填参数、文件过大等） |
| 401 | API Key 无效或缺失 |
| 422 | 场景不存在或参数组合不合法 |
| 503 | 服务暂不可用 |

### 文件大小限制

| 类型 | 限制 |
|------|------|
| 图片 | 10MB |
| 视频 | 500MB |

### H5 工具 → API 映射

| H5 路由 | API 接口 | 参数 |
|---------|---------|------|
| `/tool/ai-video` | `POST /api/v1/workflow/generate-advanced` | 按 mode 区分 |
| `/tool/image-to-video` | `POST /api/v1/workflow/generate-advanced` | `mode="first_frame"` |
| `/tool/short-video` | `POST /api/v1/workflow/generate-advanced` | `mode="first_frame"` |
| `/tool/strip-video` | `POST /api/v1/workflow/generate-advanced` | `mode="first_frame"` |
| `/tool/photo-dance` | `POST /api/v1/workflow/generate-advanced` | `mode="first_frame"` |
| `/tool/doll` | `POST /api/v1/workflow/generate-advanced` | `mode="first_frame"` |
| `/tool/video-pose` | `POST /api/v1/workflow/generate-advanced` | `mode="first_frame"` |
| `/tool/face-swap`（图片） | `POST /api/v1/image/transform` | `scene="face_swap"` |
| `/tool/face-swap`（视频） | `POST /api/v1/video/transform` | `scene="face_swap"` |
| `/tool/pose` | `POST /api/v1/image/transform` | `scene="pose"` |
| `/tool/shoot` | `POST /api/v1/image/transform` | `scene="shoot"` |
| `/tool/clothes` | `POST /api/v1/image/transform` | `scene="clothes"` |
| `/tool/puzzle` | `POST /api/v1/image/transform` | `scene="puzzle"` |
| `/tool/photo-edit` | `POST /api/v1/image/transform` | `scene="photo_edit"` |
| `/tool/make-her` | `POST /api/v1/image/transform` | `scene="photo_edit"` |
| `/tool/eraser` | `POST /api/v1/image/transform` | `scene="eraser"` |
| `/tool/brush` | `POST /api/v1/image/transform` | `scene="eraser"` |
