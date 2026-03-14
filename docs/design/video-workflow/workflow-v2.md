# 视频生成工作流协议设计方案 V2

## 概述

本文档定义了基于现有Chain工作流的三种视频生成模式的完整协议规范，整合现有功能并扩展新能力。

---

## 现有架构分析

### 已有的Chain工作流特性
1. **多段视频生成**: 支持将长视频拆分为多个segment，逐段生成后拼接
2. **Image Mode支持**:
   - `first_frame`: 使用上传图片作为首帧（I2V模式）
   - `face_reference`: 使用上传图片作为人脸参考（T2V + Reactor换脸）
3. **Story Mode**: 支持从父视频提取最后N帧作为运动参考，保持连贯性
4. **Auto LoRA**: 自动推荐LORA
5. **Auto Prompt**: 自动优化提示词
6. **后处理**: Upscale、Interpolation、MMAudio

### 现有数据流
```
用户请求 → /generate/chain
  ↓
上传图片到ComfyUI
  ↓
创建Chain (Redis: chain:{chain_id})
  ↓
run_chain() → 逐段生成
  ↓
每段: build_workflow() → ComfyUI → 保存视频
  ↓
concat_videos() → 最终视频
```

---

## 三种工作流模式整合方案

### 模式1: 参考面部模式 (Face Reference Mode)
**现状**: 已部分支持（通过`image_mode=face_reference`）
**输入**: 人脸参考图 + 用户prompt
**流程**: prompt拆解 → 匹配资源 → **获取首帧** → **换脸** → I2V生成视频

**获取首帧的两种方式**:
1. **方式A: LORA生图**
   - 使用图片LORA + 优化后的提示词 → T2I生成首帧 → 换脸
   - 适用场景: 需要特定风格、场景、姿势
   - 优点: 可控性强，能生成符合prompt的场景

2. **方式B: 使用现有图片**
   - 从推荐的参考图库中选择一张 → 直接换脸
   - 适用场景: 找到了合适的现成图片
   - 优点: 速度快，省去T2I生成步骤

**关键改进**:
- 当前实现是在T2V workflow中直接使用Reactor换脸
- **需要改进**: 先获取/生成首帧 → 换脸 → 再用换脸后的图作为I2V输入
- 原因: 对视频换脸效果差，只对首帧换脸效果更好

### 模式2: 参考全身模式 (Full Body Reference Mode)
**现状**: 未实现
**输入**: 全身参考图 + 用户prompt
**流程**: prompt拆解 → 匹配资源 → **获取首帧** → **换脸** → **SeeDream融合** → I2V生成视频

**获取首帧的两种方式**:
1. **方式A: LORA生图**
   - 使用图片LORA + 优化后的提示词 → T2I生成场景图 → 换脸 → SeeDream融合
   - 适用场景: 需要特定场景和构图

2. **方式B: 使用现有图片**
   - 从推荐的参考图库中选择一张 → 换脸 → SeeDream融合
   - 适用场景: 找到了合适的现成场景图

**与Face Reference的区别**:
- Face Reference: 获取首帧 → 换脸 → 直接I2V
- Full Body Reference: 获取首帧 → 换脸 → **SeeDream编辑（wearings或full_body模式）** → I2V
- SeeDream编辑步骤用于将参考图的服装、姿态等特征融合到场景中

**edit_mode选择建议**:
- 如果只需要换脸和服装风格：使用 `wearings` 模式
- 如果需要完整替换人物（包括姿势）：使用 `full_body` 模式

**需要新增**:
- SeeDream或类似技术的全身融合功能
- 新的image_mode: `full_body_reference`

### 模式3: 作为首帧模式 (First Frame Mode)
**现状**: 已支持（通过`image_mode=first_frame`）
**输入**: 首帧图 + 用户prompt
**流程**: prompt拆解 → 匹配视频LORA → 直接I2V生成视频

**无需改动**: 现有实现已满足需求

---

## 新增API端点设计

### 1. Prompt分析与资源推荐
```
POST /api/v1/workflow/analyze
```

**请求**:
```json
{
  "prompt": "一个女孩在海边跳舞，阳光明媚",
  "mode": "face_reference",  // face_reference | full_body_reference | first_frame
  "reference_image_base64": "...",  // 可选，用于辅助分析
  "video_duration": 10.0,  // 视频总时长（秒）
  "first_frame_source": "generate",  // generate | select_existing
  "language": "zh"
}
```

**first_frame_source说明**:
- `generate`: 使用LORA+提示词生成首帧（推荐图片LORA和T2I prompt）
- `select_existing`: 从现有图库中选择（推荐相似的参考图）

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
    // 用于生成首帧的图片LORA（仅当first_frame_source=generate时需要）
    "image_loras": [
      {
        "lora_id": 123,
        "name": "realistic_girl_v2",
        "trigger_words": ["realistic", "detailed face"],
        "strength": 0.8,
        "similarity": 0.92,
        "category": "style",
        "noise_stage": "high"
      }
    ],
    // 用于视频生成的视频LORA
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
    // 可选择的现有参考图（用于first_frame_source=select_existing）
    "reference_images": [
      {
        "resource_id": 789,
        "url": "https://...",
        "prompt": "girl dancing on beach",
        "similarity": 0.85,
        "thumbnail_url": "https://..."
      }
    ]
  },
  "optimized_prompts": {
    // 用于T2I生成首帧（仅当first_frame_source=generate时需要）
    "t2i_prompt": "realistic girl, detailed face, beach background, sunny day, photorealistic",
    // 用于I2V视频生成的分段prompts
    "segments": [
      {
        "duration": 3.3,
        "prompt": "girl starts dancing, gentle movements",
        "loras": [{"name": "dancing_motion_v1", "strength": 0.75}]
      },
      {
        "duration": 3.3,
        "prompt": "dancing intensifies, spinning motion",
        "loras": [{"name": "dancing_motion_v1", "strength": 0.8}]
      },
      {
        "duration": 3.4,
        "prompt": "final pose, arms raised, smiling",
        "loras": [{"name": "dancing_motion_v1", "strength": 0.7}]
      }
    ]
  }
}
```

**实现要点**:
- 使用现有的`EmbeddingService`进行语义搜索
- 使用现有的`LoraClassifier`进行LORA分类
- 使用LLM（Qwen3-14B）进行prompt拆解和优化
- 根据mode调整推荐策略（face_reference需要图片LORA+视频LORA）

---

### 2. T2I首帧生成（新增）
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
  "width": 832,
  "height": 480,
  "seed": 42,
  "steps": 30,
  "cfg": 7.0,
  "t2i_model": "flux_dev"  // flux_dev | sd3 | sdxl
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

**实现要点**:
- 需要集成Flux Dev或SD3等T2I模型到ComfyUI
- 创建新的T2I workflow模板
- 支持图片LORA注入
- 保存生成的图片到COS

---

### 3. 首帧换脸（新增）
```
POST /api/v1/workflow/face-swap-image
```

**请求**:
```json
{
  "source_image_url": "https://cos.../first_frame.png",
  "reference_face_url": "https://...",
  "strength": 1.0,
  "gender_detect": "auto"
}
```

**响应**:
```json
{
  "task_id": "swap_abc123",
  "status": "queued",
  "swapped_image_url": null,
  "estimated_time": 5
}
```

**实现要点**:
- 使用现有的Reactor节点
- 创建专门的图片换脸workflow（不是视频换脸）
- 保存换脸后的图片到COS

---

### 4. SeeDream图片编辑（新增）
```
POST /api/v1/workflow/seedream-edit
```

**请求**:
```json
{
  "scene_image_url": "https://cos.../scene.png",
  "reference_image_url": "https://...",  // 参考人物图
  "edit_mode": "full_body",  // face_only | wearings | full_body
  "user_prompt": "girl dancing on beach",  // 用户原始prompt
  "strength": 0.8,
  "seed": null
}
```

**edit_mode说明**:
- `face_only`: 仅替换人脸（已通过Reactor完成，此模式不需要SeeDream）
- `wearings`: 替换人体特征（服装、发型、配饰等），保持场景和姿势
- `full_body`: 完整替换人物（姿势、服装、体型等），保持场景背景

**内置提示词模板**:
```python
SEEDREAM_PROMPTS = {
    "wearings": "keep the pose and scene, replace clothing, hairstyle and accessories with reference image style, maintain body position",
    "full_body": "keep the background scene and lighting, replace the entire person including pose, clothing and body with reference image character"
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

**实现要点**:
- 使用SeeDream的图片编辑能力（支持多图输入）
- 根据edit_mode自动选择内置提示词模板
- 输入：场景图 + 参考人物图 + 提示词
- 通过调整提示词控制替换程度
- 保存编辑后的图片到COS

---

### 5. 完整工作流编排（扩展现有Chain）
```
POST /api/v1/workflow/generate-advanced
```

**请求**:
```json
{
  "mode": "face_reference",  // face_reference | full_body_reference | first_frame
  "user_prompt": "一个女孩在海边跳舞，阳光明媚",
  "reference_image": "base64_or_url",  // 人脸/全身参考图

  // 首帧获取方式
  "first_frame_source": "generate",  // generate | select_existing
  "selected_image_url": null,  // 当first_frame_source=select_existing时，指定选中的图片URL

  // 自动化选项
  "auto_analyze": true,  // 自动分析prompt并推荐资源
  "auto_lora": true,
  "auto_prompt": true,

  // 手动覆盖（可选）
  "manual_loras": [],
  "manual_segments": [],  // 手动指定分段

  // T2I首帧生成参数（仅当first_frame_source=generate时使用）
  "t2i_params": {
    "model": "flux_dev",
    "steps": 30,
    "cfg": 7.0
  },

  // 换脸参数（face_reference和full_body_reference模式）
  "face_swap_params": {
    "strength": 1.0,
    "gender_detect": "auto"
  },

  // SeeDream编辑参数（仅full_body_reference模式）
  "seedream_params": {
    "edit_mode": "full_body",  // wearings | full_body
    "strength": 0.8
  },

  // 视频生成参数（复用现有Chain参数）
  "video_params": {
    "model": "A14B",
    "width": 832,
    "height": 480,
    "fps": 24,
    "steps": 20,
    "cfg": 6.0,
    "total_duration": 10.0,
    "segment_duration": 3.3
  },

  // 后处理（复用现有）
  "post_processing": {
    "enable_upscale": false,
    "enable_interpolation": false,
    "enable_mmaudio": false
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
      "status": "completed",
      "result": {...}
    },
    {
      "name": "first_frame_acquisition",  // 获取首帧（生成或选择）
      "status": "running",
      "sub_stage": "t2i_generation",  // t2i_generation | image_selection
      "task_id": "t2i_xyz789",
      "progress": 0.45
    },
    {
      "name": "face_swap",
      "status": "pending"
    },
    {
      "name": "seedream_edit",  // 仅full_body_reference模式
      "status": "pending"
    },
    {
      "name": "video_generation",
      "status": "pending"
    }
  ],
  "chain_id": null,  // 视频生成阶段会创建chain
  "final_video_url": null,
  "estimated_total_time": 180
}
```

**工作流状态查询**:
```
GET /api/v1/workflow/{workflow_id}
```

---

## 工作流执行逻辑

### Face Reference模式完整流程

#### 方式A: LORA生成首帧
```
1. 用户上传人脸参考图 + prompt
   ↓
2. [可选] POST /workflow/analyze
   - first_frame_source = "generate"
   - 分析prompt
   - 推荐图片LORA（用于T2I）
   - 推荐视频LORA（用于I2V）
   - 生成优化后的T2I prompt和I2V segments
   ↓
3. POST /workflow/generate-first-frame
   - 使用T2I模型 + 图片LORA生成首帧
   - 提示词要求正脸（除非是后背场景）
   - 保存首帧图到COS
   ↓
4. POST /workflow/face-swap-image
   - 使用Reactor对首帧图换脸
   - 输入: 生成的首帧 + 用户的人脸参考图
   - 保存换脸后的图到COS
   ↓
5. POST /generate/chain (复用现有)
   - image_mode = "first_frame"
   - image = 换脸后的首帧图
   - segments = 分析阶段生成的segments
   - loras = 视频LORA（不是图片LORA）
   - 逐段生成视频并拼接
   ↓
6. [可选] 后处理
   - Upscale
   - Interpolation
   - MMAudio
   ↓
7. 返回最终视频URL
```

#### 方式B: 选择现有图片
```
1. 用户上传人脸参考图 + prompt
   ↓
2. POST /workflow/analyze
   - first_frame_source = "select_existing"
   - 分析prompt
   - 推荐相似的参考图（不推荐图片LORA）
   - 推荐视频LORA（用于I2V）
   - 生成优化后的I2V segments
   ↓
3. 用户从推荐的参考图中选择一张
   ↓
4. POST /workflow/face-swap-image
   - 使用Reactor对选中的图片换脸
   - 输入: 选中的参考图 + 用户的人脸参考图
   - 保存换脸后的图到COS
   ↓
5. POST /generate/chain (复用现有)
   - image_mode = "first_frame"
   - image = 换脸后的首帧图
   - segments = 分析阶段生成的segments
   - loras = 视频LORA
   - 逐段生成视频并拼接
   ↓
6. [可选] 后处理
   ↓
7. 返回最终视频URL
```

### Full Body Reference模式完整流程

#### 方式A: LORA生成首帧
```
1. 用户上传全身参考图 + prompt
   ↓
2. [可选] POST /workflow/analyze
   - first_frame_source = "generate"
   - 推荐图片LORA和视频LORA
   ↓
3. POST /workflow/generate-first-frame
   - 生成场景图（可能含通用人物）
   ↓
4. POST /workflow/face-swap-image
   - 对生成的场景图换脸
   ↓
5. POST /workflow/seedream-edit
   - 将用户的全身参考图特征融合到换脸后的场景图中
   - edit_mode = "wearings" 或 "full_body"
   - 融合服装、姿态、体型等特征
   - 保存编辑后的图到COS
   ↓
6. POST /generate/chain
   - image_mode = "first_frame"
   - image = 融合后的首帧图
   - 逐段生成视频
   ↓
7. [可选] 后处理
   ↓
8. 返回最终视频URL
```

#### 方式B: 选择现有图片
```
1. 用户上传全身参考图 + prompt
   ↓
2. POST /workflow/analyze
   - first_frame_source = "select_existing"
   - 推荐相似的参考图
   ↓
3. 用户从推荐的参考图中选择一张
   ↓
4. POST /workflow/face-swap-image
   - 对选中的图片换脸
   ↓
5. POST /workflow/seedream-edit
   - 将用户的全身参考图特征融合到换脸后的图中
   - edit_mode = "wearings" 或 "full_body"
   - 保存编辑后的图到COS
   ↓
6. POST /generate/chain
   - image_mode = "first_frame"
   - image = 融合后的首帧图
   - 逐段生成视频
   ↓
7. [可选] 后处理
   ↓
8. 返回最终视频URL
```

### First Frame模式完整流程

```
1. 用户上传首帧图 + prompt
   ↓
2. [可选] POST /workflow/analyze
   - 只推荐视频LORA
   ↓
3. POST /generate/chain (复用现有)
   - image_mode = "first_frame"
   - image = 用户上传的首帧图
   - 逐段生成视频
   ↓
4. [可选] 后处理
   ↓
5. 返回最终视频URL
```

---

## 数据库Schema扩展

### 新表: advanced_workflows
```sql
CREATE TABLE advanced_workflows (
    id VARCHAR(64) PRIMARY KEY,
    mode VARCHAR(32) NOT NULL,  -- face_reference | full_body_reference | first_frame
    user_prompt TEXT NOT NULL,
    reference_image_url VARCHAR(512),

    -- 首帧获取方式
    first_frame_source VARCHAR(32),  -- generate | select_existing
    selected_image_url VARCHAR(512),  -- 用户选择的现有图片URL

    -- 阶段状态
    current_stage VARCHAR(64),
    status VARCHAR(32),  -- running | completed | failed | paused

    -- 各阶段任务ID
    analysis_result JSON,
    t2i_task_id VARCHAR(64),  -- T2I生成任务（仅first_frame_source=generate时有值）
    face_swap_task_id VARCHAR(64),
    seedream_task_id VARCHAR(64),  -- SeeDream编辑任务（仅full_body_reference模式）
    chain_id VARCHAR(64),  -- 关联到现有的chain

    -- 中间结果
    first_frame_url VARCHAR(512),  -- 生成或选择的原始首帧
    face_swapped_url VARCHAR(512),  -- 换脸后的图
    seedream_edited_url VARCHAR(512),  -- SeeDream编辑后的图（仅full_body_reference模式）
    final_video_url VARCHAR(512),

    -- 元数据
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

## 实现优先级

### Phase 1: 核心基础设施（1-2周）
1. ✅ 复用现有Chain工作流（已完成）
2. 🔨 实现 `/workflow/analyze` API
   - 集成EmbeddingService语义搜索
   - 集成LoraClassifier分类
   - 使用Qwen3-14B进行prompt拆解和优化
3. 🔨 集成T2I模型到ComfyUI
   - 下载Flux Dev或SD3模型
   - 创建T2I workflow模板
   - 实现 `/workflow/generate-first-frame` API

### Phase 2: Face Reference模式（1周）
1. 🔨 实现 `/workflow/face-swap-image` API
   - 创建图片换脸workflow（复用Reactor节点）
2. 🔨 实现 `/workflow/generate-advanced` API
   - 编排Face Reference完整流程
   - 状态管理和进度追踪
3. 🔨 测试端到端流程

### Phase 3: First Frame模式（3天）
1. ✅ 复用现有Chain的first_frame模式（已完成）
2. 🔨 集成到 `/workflow/generate-advanced` API
3. 🔨 测试

### Phase 4: Full Body Reference模式（2-3周）
1. 🔨 集成SeeDream到ComfyUI
   - 下载SeeDream模型
   - 创建图片编辑workflow
   - 实现多图输入和提示词控制
2. 🔨 实现 `/workflow/seedream-edit` API
   - 支持wearings和full_body两种edit_mode
   - 内置提示词模板
3. 🔨 集成到 `/workflow/generate-advanced` API
4. 🔨 测试不同edit_mode的效果

### Phase 5: 前端UI（2周）
1. 模式选择界面
2. 资源推荐和预览界面
3. 进度追踪界面
4. 结果展示和分享界面

### Phase 6: 优化和扩展（持续）
1. 性能优化（并行处理、缓存）
2. 成本优化（GPU使用时间统计）
3. A/B测试不同策略
4. 用户反馈收集和迭代

---

## 技术栈整合

### 复用现有组件
- ✅ Chain工作流（多段视频生成）
- ✅ EmbeddingService（语义搜索）
- ✅ LoraClassifier（LORA分类）
- ✅ Reactor（换脸）
- ✅ Wan2.2 I2V（视频生成）
- ✅ Redis（状态管理）
- ✅ Tencent COS（存储）
- ✅ Qwen3-14B（LLM）

### 需要新增
- 🔨 T2I模型（Flux Dev / SD3）
- 🔨 T2I workflow模板
- 🔨 图片换脸workflow
- 🔨 SeeDream图片编辑（支持多图输入和提示词控制）
- 🔨 高级工作流编排逻辑

---

## 关键改进点

### 1. 换脸只用于首帧图
- ❌ 旧方案: 在视频生成后对整个视频换脸（效果差）
- ✅ 新方案: 获取/生成首帧 → 换脸 → 用换脸后的图作为I2V输入

### 2. 两种获取首帧的方式
- ✅ **方式A: LORA生成** - 使用图片LORA+提示词生成首帧，可控性强
- ✅ **方式B: 选择现有图片** - 从推荐的图库中选择，速度快

### 3. Full Body Reference的特殊处理
- Face Reference: 获取首帧 → 换脸 → I2V
- Full Body Reference: 获取首帧 → 换脸 → **SeeDream编辑** → I2V
- SeeDream编辑通过提示词控制替换程度：
  - `wearings`模式：替换服装、发型、配饰，保持姿势
  - `full_body`模式：完整替换人物（姿势、服装、体型），保持场景

### 4. 整合现有Chain工作流
- ❌ 旧方案: 创建全新的工作流系统
- ✅ 新方案: 扩展现有Chain，复用多段生成、后处理等功能

### 5. 模块化设计
- 每个阶段独立API端点，可单独调用
- 也可通过 `/workflow/generate-advanced` 一键编排

### 6. 灵活的自动化程度
- 用户可选择全自动（auto_analyze + auto_lora + auto_prompt）
- 也可手动指定LORA和segments
- 支持首帧来源选择（生成 vs 选择现有）

---

## API调用示例

### 示例1: 全自动Face Reference模式
```bash
# 一键生成
curl -X POST https://api.example.com/api/v1/workflow/generate-advanced \
  -H "X-API-Key: xxx" \
  -F "mode=face_reference" \
  -F "user_prompt=一个女孩在海边跳舞" \
  -F "reference_image=@face.jpg" \
  -F 'params={
    "auto_analyze": true,
    "auto_lora": true,
    "auto_prompt": true,
    "video_params": {
      "total_duration": 10.0
    }
  }'

# 响应
{
  "workflow_id": "wf_abc123",
  "status": "running",
  "estimated_total_time": 180
}

# 轮询状态
curl https://api.example.com/api/v1/workflow/wf_abc123
```

### 示例2: Face Reference - LORA生成首帧（手动控制）
```bash
# 步骤1: 分析prompt
curl -X POST /api/v1/workflow/analyze \
  -d '{
    "prompt": "一个女孩在海边跳舞",
    "mode": "face_reference",
    "first_frame_source": "generate"
  }'

# 步骤2: 生成首帧（使用推荐的LORA）
curl -X POST /api/v1/workflow/generate-first-frame \
  -d '{
    "prompt": "realistic girl, beach background, sunny",
    "loras": [{"name": "realistic_girl_v2", "strength": 0.8}]
  }'

# 步骤3: 换脸
curl -X POST /api/v1/workflow/face-swap-image \
  -d '{
    "source_image_url": "https://cos.../generated_frame.jpg",
    "reference_face_url": "https://user-upload.../face.jpg"
  }'

# 步骤4: 生成视频（复用现有Chain）
curl -X POST /api/v1/generate/chain \
  -F "image=@swapped_frame.png" \
  -F 'params={"image_mode": "first_frame", "segments": [...]}'
```

### 示例3: Face Reference - 选择现有图片
```bash
# 步骤1: 分析并获取推荐图片
curl -X POST /api/v1/workflow/analyze \
  -d '{
    "prompt": "一个女孩在海边跳舞",
    "mode": "face_reference",
    "first_frame_source": "select_existing"
  }'

# 响应包含推荐的参考图
{
  "recommendations": {
    "reference_images": [
      {"resource_id": 789, "url": "https://cos.../beach_1.jpg", "similarity": 0.92},
      {"resource_id": 790, "url": "https://cos.../beach_2.jpg", "similarity": 0.88}
    ]
  }
}

# 步骤2: 用户选择一张图片，直接换脸
curl -X POST /api/v1/workflow/face-swap-image \
  -d '{
    "source_image_url": "https://cos.../beach_1.jpg",
    "reference_face_url": "https://user-upload.../face.jpg"
  }'

# 步骤3: 生成视频
curl -X POST /api/v1/generate/chain \
  -F "image=@swapped_frame.png" \
  -F 'params={"image_mode": "first_frame", ...}'
```

### 示例4: Full Body Reference - LORA生成首帧
```bash
curl -X POST /api/v1/workflow/generate-advanced \
  -H "X-API-Key: xxx" \
  -F "mode=full_body_reference" \
  -F "user_prompt=一个女孩穿红裙子在海边跳舞" \
  -F "reference_image=@fullbody.jpg" \
  -F 'params={
    "first_frame_source": "generate",
    "auto_analyze": true,
    "seedream_params": {
      "edit_mode": "full_body",
      "strength": 0.8
    }
  }'
```

### 示例5: Full Body Reference - 选择现有图片
```bash
# 步骤1: 分析并获取推荐
curl -X POST /api/v1/workflow/analyze \
  -d '{
    "prompt": "女孩穿红裙子在海边跳舞",
    "mode": "full_body_reference",
    "first_frame_source": "select_existing"
  }'

# 步骤2: 选择图片并换脸
curl -X POST /api/v1/workflow/face-swap-image \
  -d '{
    "source_image_url": "https://cos.../selected.jpg",
    "reference_face_url": "https://user-upload.../fullbody.jpg"
  }'

# 步骤3: SeeDream编辑（替换服装或全身）
curl -X POST /api/v1/workflow/seedream-edit \
  -d '{
    "scene_image_url": "https://cos.../face_swapped.jpg",
    "reference_image_url": "https://user-upload.../fullbody.jpg",
    "edit_mode": "wearings",
    "user_prompt": "girl in red dress dancing on beach",
    "strength": 0.8
  }'

# 步骤4: 生成视频
curl -X POST /api/v1/generate/chain \
  -F "image=@edited_frame.jpg" \
  -F 'params={"image_mode": "first_frame", ...}'
```

---

## 下一步行动

1. **用户确认**: 确认此V2方案是否符合需求
   - 两种获取首帧的方式（LORA生成 vs 选择现有图片）
   - Face Reference和Full Body Reference的区别（是否需要SeeDream编辑）
   - SeeDream的两种edit_mode（wearings vs full_body）
   - API设计和工作流编排

2. **技术调研**:
   - 确认T2I模型选择（Flux Dev vs SD3 vs SDXL）
   - 确认SeeDream集成方案（ComfyUI节点、API调用等）
   - 测试SeeDream的提示词控制效果
   - 评估各方案的效果、性能、成本

3. **原型开发**:
   - Phase 1: 实现analyze API和T2I集成（1-2周）
   - Phase 2: 实现Face Reference完整流程（1周）
   - Phase 3: 实现First Frame模式集成（3天）
   - Phase 4: 实现Full Body Reference和SeeDream编辑（2-3周）

4. **测试验证**:
   - 使用真实数据测试端到端流程
   - 对比两种获取首帧方式的效果和速度
   - 验证换脸质量和视频连贯性
   - 测试SeeDream的wearings和full_body模式效果差异

5. **迭代优化**:
   - 根据测试结果调整协议和实现
   - 优化推荐算法（LORA匹配、图片搜索）
   - 优化SeeDream提示词模板
   - 性能优化和成本控制

---

## 总结

本V2方案的核心改进：

1. **支持两种获取首帧的方式**
   - LORA生成：适合需要特定风格和场景
   - 选择现有图片：适合找到合适的参考图，速度更快

2. **明确Face Reference和Full Body Reference的区别**
   - Face Reference: 首帧 → 换脸 → I2V
   - Full Body Reference: 首帧 → 换脸 → SeeDream编辑 → I2V

3. **SeeDream图片编辑能力**
   - 支持多图输入（场景图 + 参考人物图）
   - 通过内置提示词模板控制编辑程度：
     - `wearings`模式：替换服装、发型、配饰，保持姿势
     - `full_body`模式：完整替换人物（姿势、服装、体型），保持场景
   - 无需调研替代方案，直接使用SeeDream的图片编辑功能

4. **整合现有Chain工作流**
   - 复用多段生成、Story Mode、后处理等成熟功能
   - 减少重复开发，加快上线速度

5. **模块化和灵活性**
   - 独立API端点，支持分步调用
   - 一键编排API，支持全自动生成
   - 用户可选择自动化程度

---

**文档版本**: v2.2
**创建日期**: 2026-03-13
**更新说明**:
- 整合现有Chain工作流
- 修正换脸逻辑（只对首帧换脸）
- 新增两种获取首帧的方式（LORA生成 vs 选择现有图片）
- 明确Full Body Reference需要SeeDream编辑步骤
- **使用SeeDream的图片编辑能力，支持wearings和full_body两种模式**
- **通过内置提示词模板控制编辑程度**
- 模块化API设计
- 明确实现优先级
