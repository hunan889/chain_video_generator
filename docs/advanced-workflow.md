# 视频生成工作流完整方案（最终版）

## 概述

本文档定义了基于现有Chain工作流的三种视频生成模式的完整协议规范，整合现有功能并扩展新能力。

**核心原则**:
- 整合现有Chain工作流，复用多段生成、Story Mode、后处理等功能
- 换脸只用于首帧图，不对视频换脸
- 支持三种首帧获取方式：上传图片、LORA生成、选择现有图片
- 使用SeeDream图片编辑实现不同程度的人物替换
- 完全自动化流程，无需中间确认

---

## 三种工作流模式

### 模式1: 参考面部模式 (Face Reference Mode)
**输入**: 人脸参考图 + 用户prompt
**流程**: prompt拆解 → 匹配资源 → **获取首帧** → **换脸/SeeDream编辑** → I2V生成视频

**获取首帧的三种方式**:
1. **方式A: 上传图片作为首帧**（默认）
   - 用户直接上传首帧图 → 换脸/SeeDream编辑 → I2V
   - 适用场景: 用户已有合适的首帧图
   - 优点: 最快，无需生成

2. **方式B: LORA生成首帧**
   - 使用图片LORA + 优化后的提示词 → T2I生成首帧 → 换脸/SeeDream编辑 → I2V
   - 适用场景: 需要特定风格、场景、姿势
   - 优点: 可控性强，能生成符合prompt的场景

3. **方式C: 选择现有图片**
   - 从推荐的参考图库中选择一张 → 换脸/SeeDream编辑 → I2V
   - 适用场景: 找到了合适的现成图片
   - 优点: 速度快，省去T2I生成步骤

**SeeDream编辑模式**:
- `face_only`: 仅换脸（SeeDream换脸，可能比Reactor效果更好）
- `face_wearings`: 换脸 + 换首饰（发型、耳环、项链、眼镜等）
- `full_body`: 换脸 + 换首饰 + 换服装

### 模式2: 参考全身模式 (Full Body Reference Mode)
**输入**: 全身参考图 + 用户prompt
**流程**: prompt拆解 → 匹配资源 → **获取首帧** → **换脸/SeeDream编辑** → I2V生成视频

**与Face Reference的区别**:
- Face Reference: 主要关注面部和上半身
- Full Body Reference: 关注全身（姿势、服装、体型）
- 两者都使用SeeDream编辑，只是edit_mode选择不同

### 模式3: 作为首帧模式 (First Frame Mode)
**输入**: 首帧图 + 用户prompt
**流程**: prompt拆解 → 匹配视频LORA → 直接I2V生成视频

**特点**:
- 不需要换脸或SeeDream编辑
- 直接使用上传的首帧图生成视频
- 最简单快速的模式

---

## 技术规范

### 1. 分辨率和时长规范

#### 图片分辨率预设
```python
RESOLUTION_PRESETS = {
    "480p_4:3": {"width": 640, "height": 480},
    "480p_3:4": {"width": 480, "height": 640},
    "720p_4:3": {"width": 960, "height": 720},
    "720p_3:4": {"width": 720, "height": 960},
    "1080p_4:3": {"width": 1440, "height": 1080},
    "1080p_3:4": {"width": 1080, "height": 1440},
}
```

#### 视频时长预设
```python
DURATION_PRESETS = {
    "5s": {"duration": 5.0, "num_frames": 121},  # 24fps * 5s + 1
    "10s": {"duration": 10.0, "num_frames": 241},  # 24fps * 10s + 1
}
```

**固定参数**:
- 帧率: 24fps
- 比例: 3:4 或 4:3

### 2. LORA类型区分

**图片LORA** (用于T2I首帧生成):
- 数据库表: `image_lora_metadata`
- 存储目录: 独立目录（与视频LORA分开）
- 用途: SD WebUI/Flux Dev生成首帧图

**视频LORA** (用于I2V视频生成):
- 数据库表: `lora_metadata`
- 存储目录: `ComfyUI/models/loras/`
- 字段: `mode` (I2V/T2V/both), `noise_stage` (high/low/single)
- 用途: Wan2.2 I2V生成视频

### 3. SeeDream图片编辑

**API**: 已集成BytePlus SeeDream API

**三种edit_mode**:
```python
SEEDREAM_EDIT_PROMPTS = {
    "face_only": "keep the entire scene, body, clothing and accessories, only replace the face with reference image, maintain all other features exactly",

    "face_wearings": "keep the scene, body pose and clothing, replace the face and accessories (jewelry, glasses, hair accessories) with reference image style",

    "full_body": "keep the background scene and lighting, replace the face, accessories and clothing with reference image character, maintain body pose"
}
```

**换脸开关**:
- `enable_reactor_first`: 是否先用Reactor换脸再SeeDream编辑
- 开启: Reactor换脸 → SeeDream编辑（增强面部一致性）
- 关闭: 直接SeeDream编辑

### 4. T2I模型选择

**短期方案**: SD WebUI + PONY NSFW模型 + PONY NSFW LORA
- 已有成熟的排队机制
- 已有LORA生态
- 团队熟悉

**中期扩展**: 预留Flux Dev接口
- 代码设计支持模型切换
- 通过配置文件选择模型
- 后续可无缝切换

---

## API设计

### 1. Prompt分析与资源推荐
```
POST /api/v1/workflow/analyze
```

**请求**:
```json
{
  "prompt": "一个女孩在海边跳舞，阳光明媚",
  "mode": "face_reference",
  "reference_image_base64": "...",
  "video_duration": 10.0,
  "first_frame_source": "use_uploaded",
  "language": "zh"
}
```

**first_frame_source选项**:
- `use_uploaded`: 使用上传的图片作为首帧（默认）
- `generate`: 使用LORA+提示词生成首帧
- `select_existing`: 从现有图库中选择

**响应**:
```json
{
  "analysis": {
    "scene": {
      "description": "海边场景，阳光明媚",
      "keywords": ["beach", "sunny", "outdoor"]
    },
    "action": {
      "description": "跳舞动作",
      "keywords": ["dancing", "movement"]
    },
    "character": {
      "description": "女孩",
      "keywords": ["girl", "female"],
      "pose_requirement": "正脸"
    }
  },
  "recommendations": {
    "image_loras": [
      {
        "lora_id": 123,
        "name": "realistic_girl_v2",
        "trigger_words": ["realistic", "detailed face"],
        "strength": 0.8,
        "similarity": 0.92,
        "category": "style"
      }
    ],
    "video_loras": [
      {
        "lora_id": 456,
        "name": "dancing_motion_v1",
        "trigger_words": ["dancing", "fluid motion"],
        "strength": 0.75,
        "similarity": 0.88,
        "category": "action",
        "noise_stage": "high"
      }
    ],
    "reference_images": [
      {
        "resource_id": 789,
        "url": "https://...",
        "prompt": "girl dancing on beach",
        "similarity": 0.85
      }
    ]
  },
  "optimized_prompts": {
    "t2i_prompt": "realistic girl, detailed face, front view, beach background, sunny day, photorealistic",
    "segments": [
      {
        "duration": 3.3,
        "prompt": "girl starts dancing, gentle movements",
        "loras": [{"name": "dancing_motion_v1", "strength": 0.75}]
      }
    ]
  }
}
```

### 2. T2I首帧生成（可选）
```
POST /api/v1/workflow/generate-first-frame
```

**请求**:
```json
{
  "prompt": "realistic girl, detailed face, beach background...",
  "negative_prompt": "blurry, low quality",
  "loras": [
    {"name": "realistic_girl_v2", "strength": 0.8}
  ],
  "resolution": "720p_3:4",
  "seed": 42,
  "steps": 30,
  "cfg": 7.0,
  "t2i_model": "pony_nsfw"
}
```

**响应**:
```json
{
  "task_id": "t2i_xyz789",
  "status": "queued",
  "first_frame_url": null,
  "estimated_time": 15
}
```

### 3. SeeDream图片编辑
```
POST /api/v1/workflow/seedream-edit
```

**请求**:
```json
{
  "scene_image_url": "https://cos.../scene.png",
  "reference_image_url": "https://...",
  "edit_mode": "face_wearings",
  "enable_reactor_first": true,
  "user_prompt": "girl dancing on beach",
  "strength": 0.8,
  "seed": null
}
```

**响应**:
```json
{
  "task_id": "seedream_def456",
  "status": "queued",
  "edited_image_url": null,
  "estimated_time": 10
}
```

### 4. 完整工作流编排
```
POST /api/v1/workflow/generate-advanced
```

**请求**:
```json
{
  "mode": "face_reference",
  "user_prompt": "一个女孩在海边跳舞",
  "reference_image": "base64_or_url",

  "first_frame_source": "use_uploaded",
  "uploaded_first_frame": "base64_or_url",
  "selected_image_url": null,

  "auto_analyze": true,
  "auto_lora": true,
  "auto_prompt": true,

  "t2i_params": {
    "model": "pony_nsfw",
    "steps": 30,
    "cfg": 7.0
  },

  "seedream_params": {
    "edit_mode": "face_wearings",
    "enable_reactor_first": true,
    "strength": 0.8
  },

  "video_params": {
    "model": "A14B",
    "resolution": "720p_3:4",
    "duration": "5s",
    "steps": 20,
    "cfg": 6.0,
    "enable_audio": false
  }
}
```

**响应**:
```json
{
  "workflow_id": "wf_abc123",
  "status": "running",
  "current_stage": "prompt_analysis",
  "stages": [
    {
      "name": "prompt_analysis",
      "status": "completed"
    },
    {
      "name": "first_frame_acquisition",
      "status": "running",
      "sub_stage": "use_uploaded"
    },
    {
      "name": "seedream_edit",
      "status": "pending"
    },
    {
      "name": "video_generation",
      "status": "pending"
    }
  ],
  "chain_id": null,
  "final_video_url": null,
  "estimated_total_time": 120
}
```

---

## 工作流执行逻辑

### Face Reference模式 - 方式A（上传首帧）
```
1. 用户上传人脸参考图 + 首帧图 + prompt
   ↓
2. [可选] POST /workflow/analyze
   - 分析prompt
   - 推荐视频LORA
   - 生成优化后的I2V segments
   ↓
3. POST /workflow/seedream-edit
   - 输入: 首帧图 + 人脸参考图
   - edit_mode: face_only / face_wearings / full_body
   - enable_reactor_first: true/false
   ↓
4. POST /generate/chain
   - image_mode = "first_frame"
   - image = SeeDream编辑后的图
   - segments = 分析阶段生成的segments
   - loras = 视频LORA
   ↓
5. [可选] 后处理 (Upscale/Interpolation/MMAudio)
   ↓
6. 返回最终视频URL
```

### Face Reference模式 - 方式B（LORA生成首帧）
```
1. 用户上传人脸参考图 + prompt
   ↓
2. POST /workflow/analyze
   - first_frame_source = "generate"
   - 推荐图片LORA和视频LORA
   ↓
3. POST /workflow/generate-first-frame
   - 使用T2I模型 + 图片LORA生成首帧
   - 提示词强调正脸
   ↓
4. POST /workflow/seedream-edit
   - 输入: 生成的首帧 + 人脸参考图
   ↓
5. POST /generate/chain
   - 生成视频
   ↓
6. 返回最终视频URL
```

### Face Reference模式 - 方式C（选择现有图片）
```
1. 用户上传人脸参考图 + prompt
   ↓
2. POST /workflow/analyze
   - first_frame_source = "select_existing"
   - 推荐相似的参考图
   ↓
3. 用户从推荐图中选择一张
   ↓
4. POST /workflow/seedream-edit
   - 输入: 选中的图 + 人脸参考图
   ↓
5. POST /generate/chain
   - 生成视频
   ↓
6. 返回最终视频URL
```

### First Frame模式
```
1. 用户上传首帧图 + prompt
   ↓
2. [可选] POST /workflow/analyze
   - 只推荐视频LORA
   ↓
3. POST /generate/chain
   - image_mode = "first_frame"
   - image = 用户上传的首帧图
   - 直接生成视频（无需换脸或编辑）
   ↓
4. [可选] 后处理
   ↓
5. 返回最终视频URL
```

---

## 数据库Schema

### 新表: advanced_workflows
```sql
CREATE TABLE advanced_workflows (
    id VARCHAR(64) PRIMARY KEY,
    mode VARCHAR(32) NOT NULL,
    user_prompt TEXT NOT NULL,
    reference_image_url VARCHAR(512),

    first_frame_source VARCHAR(32),
    uploaded_first_frame_url VARCHAR(512),
    selected_image_url VARCHAR(512),

    current_stage VARCHAR(64),
    status VARCHAR(32),

    analysis_result JSON,
    t2i_task_id VARCHAR(64),
    seedream_task_id VARCHAR(64),
    chain_id VARCHAR(64),

    first_frame_url VARCHAR(512),
    edited_frame_url VARCHAR(512),
    final_video_url VARCHAR(512),

    error_message TEXT,
    created_at BIGINT,
    updated_at BIGINT,
    completed_at BIGINT,

    INDEX idx_status (status),
    INDEX idx_chain_id (chain_id),
    INDEX idx_created_at (created_at)
);
```

---

## 提示词优化策略

### T2I提示词优化
```python
def optimize_t2i_prompt(user_prompt, selected_loras, pose_requirement):
    # 1. 注入LORA trigger words
    trigger_words = []
    for lora in selected_loras:
        trigger_words.extend(lora.trigger_words)

    # 2. 添加姿势要求
    pose_hints = {
        "正脸": "front view, facing camera, looking at viewer",
        "侧脸": "side view, profile",
        "后背": "back view, from behind"
    }

    # 3. 构建优化后的提示词
    optimized = f"{user_prompt}, {', '.join(trigger_words)}"
    if pose_requirement in pose_hints:
        optimized += f", {pose_hints[pose_requirement]}"

    # 4. 使用LLM进一步优化
    optimized = await llm_optimize(optimized, mode="t2i")

    return optimized
```

### I2V提示词优化
```python
def optimize_i2v_prompt(user_prompt, first_frame_description, video_loras):
    # 1. 注入视频LORA trigger words
    trigger_words = []
    for lora in video_loras:
        trigger_words.extend(lora.trigger_words)

    # 2. 构建优化后的提示词
    optimized = f"{user_prompt}, {', '.join(trigger_words)}"

    # 3. 确保与首帧一致
    optimized += f", consistent with: {first_frame_description}"

    # 4. 使用LLM优化
    optimized = await llm_optimize(optimized, mode="i2v")

    return optimized
```

### SeeDream提示词（内置模板）
```python
SEEDREAM_EDIT_PROMPTS = {
    "face_only": "keep the entire scene, body, clothing and accessories, only replace the face with reference image, maintain all other features exactly",

    "face_wearings": "keep the scene, body pose and clothing, replace the face and accessories (jewelry, glasses, hair accessories) with reference image style",

    "full_body": "keep the background scene and lighting, replace the face, accessories and clothing with reference image character, maintain body pose"
}

def build_seedream_prompt(user_prompt, edit_mode):
    template = SEEDREAM_EDIT_PROMPTS[edit_mode]
    return f"{user_prompt}, {template}"
```

---

## 错误处理和降级策略

### T2I生成失败
```python
async def generate_first_frame_with_fallback(prompt, loras, recommended_images):
    try:
        # 尝试T2I生成
        return await generate_t2i(prompt, loras)
    except Exception as e:
        logger.warning(f"T2I failed: {e}, trying fallback")
        if recommended_images:
            # 降级到推荐图片
            return recommended_images[0]["url"]
        else:
            # 无推荐图片，报错
            raise HTTPException(500, "首帧生成失败，且无推荐图片可用")
```

### SeeDream编辑失败
```python
async def seedream_edit_with_fallback(scene_url, ref_url, edit_mode, enable_reactor):
    try:
        # 尝试SeeDream编辑
        return await seedream_edit(scene_url, ref_url, edit_mode, enable_reactor)
    except Exception as e:
        logger.warning(f"SeeDream failed: {e}, trying Reactor only")
        if enable_reactor:
            # 降级到只Reactor换脸
            return await reactor_face_swap(scene_url, ref_url)
        else:
            # 直接使用原图
            return scene_url
```

---

## 实现优先级

### Phase 1: 核心基础设施（1-2周）
1. ✅ 复用现有Chain工作流
2. 🔨 实现 `/workflow/analyze` API
   - 集成EmbeddingService语义搜索
   - 区分图片LORA和视频LORA
   - 使用Qwen3-14B进行prompt拆解和优化
3. 🔨 集成T2I模型（SD WebUI + PONY NSFW）
   - 复用现有SD WebUI排队机制
   - 创建T2I workflow模板
   - 实现 `/workflow/generate-first-frame` API

### Phase 2: SeeDream编辑（1周）
1. 🔨 实现 `/workflow/seedream-edit` API
   - 集成现有的`/image/scene-swap`功能
   - 实现三种edit_mode（face_only/face_wearings/full_body）
   - 实现换脸开关（enable_reactor_first）
   - 内置提示词模板
2. 🔨 测试不同edit_mode的效果

### Phase 3: 完整工作流编排（1周）
1. 🔨 实现 `/workflow/generate-advanced` API
   - 支持三种first_frame_source
   - 编排完整流程
   - 状态管理和进度追踪
2. 🔨 实现错误降级策略
3. 🔨 端到端测试

### Phase 4: 前端UI（2周）
1. 模式选择界面
2. 首帧来源选择界面
3. 资源推荐和预览界面
4. 进度追踪界面
5. 结果展示界面

### Phase 5: 优化和扩展（持续）
1. 性能优化
2. 提示词模板优化
3. 预留Flux Dev接口
4. 用户反馈收集和迭代

---

## 技术栈整合

### 复用现有组件
- ✅ Chain工作流（多段视频生成）
- ✅ EmbeddingService（语义搜索）
- ✅ LoraClassifier（LORA分类）
- ✅ Reactor（换脸）
- ✅ SeeDream API（图片编辑）
- ✅ Wan2.2 I2V（视频生成）
- ✅ Redis（状态管理）
- ✅ Tencent COS（存储）
- ✅ Qwen3-14B（LLM）
- ✅ SD WebUI + PONY NSFW（T2I）

### 需要新增
- 🔨 T2I workflow模板
- 🔨 SeeDream编辑API封装
- 🔨 高级工作流编排逻辑
- 🔨 分辨率和时长预设
- 🔨 提示词优化模板

---

## 关键设计决策

### 1. 默认使用上传首帧
- 最快速的方式
- 用户可控性最强
- 可选T2I生成或选择现有图片

### 2. 保留三种SeeDream edit_mode
- `face_only`: 仅换脸（SeeDream可能比Reactor效果更好）
- `face_wearings`: 换脸 + 换首饰
- `full_body`: 换脸 + 换首饰 + 换服装

### 3. 换脸开关设计
- `enable_reactor_first=true`: 先Reactor换脸再SeeDream编辑
- `enable_reactor_first=false`: 直接SeeDream编辑
- 增强面部一致性

### 4. 完全自动化
- 无需中间确认
- 通过API轮询获取进度
- 错误自动降级

### 5. 预留扩展能力
- T2I模型可切换（PONY → Flux Dev）
- 支持后续添加质量评分
- 支持后续添加成本控制

---

## API调用示例

### 示例1: Face Reference - 上传首帧（最常用）
```bash
curl -X POST /api/v1/workflow/generate-advanced \
  -H "X-API-Key: xxx" \
  -F "mode=face_reference" \
  -F "user_prompt=一个女孩在海边跳舞" \
  -F "reference_image=@face.jpg" \
  -F "uploaded_first_frame=@beach_scene.jpg" \
  -F 'params={
    "first_frame_source": "use_uploaded",
    "seedream_params": {
      "edit_mode": "face_wearings",
      "enable_reactor_first": true
    },
    "video_params": {
      "resolution": "720p_3:4",
      "duration": "5s"
    }
  }'
```

### 示例2: Face Reference - LORA生成首帧
```bash
curl -X POST /api/v1/workflow/generate-advanced \
  -F "mode=face_reference" \
  -F "first_frame_source=generate" \
  -F "reference_image=@face.jpg" \
  -F 'params={
    "auto_analyze": true,
    "t2i_params": {
      "model": "pony_nsfw",
      "steps": 30
    },
    "seedream_params": {
      "edit_mode": "face_only"
    }
  }'
```

### 示例3: First Frame - 直接生成视频
```bash
curl -X POST /api/v1/generate/chain \
  -F "image=@first_frame.jpg" \
  -F 'params={
    "image_mode": "first_frame",
    "prompt": "girl dancing on beach",
    "segments": [
      {"prompt": "starts dancing", "duration": 3.3},
      {"prompt": "spinning motion", "duration": 3.3}
    ],
    "resolution": "720p_3:4",
    "duration": "5s"
  }'
```

---

**文档版本**: v3.0 (最终版)
**创建日期**: 2026-03-13
**更新说明**:
- 整合所有讨论结果
- 明确三种首帧获取方式（默认上传）
- 明确三种SeeDream edit_mode定义
- 明确分辨率和时长规范
- 明确LORA类型区分
- 明确错误降级策略
- 预留Flux Dev扩展能力
- 删除中间文档，输出最终完整方案
