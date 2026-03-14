# Prompt 最佳实践指南

## 概述

本指南帮助用户编写高质量的 prompt，避免常见问题，生成连贯自然的视频。

---

## ❌ 常见错误

### 错误1: 使用时间标记

**错误示例**:
```
(at 0 seconds: girl standing) (at 2 seconds: girl sitting) (at 4 seconds: girl walking)
```

**问题**:
- Wan2.2 模型不支持时间标记语法
- 模型会将所有描述同时应用到所有帧
- 导致场景跳跃、不连贯

**正确做法**: 使用 Chain Mode 或 Story Mode 分段生成

---

### 错误2: 在单个 Prompt 中描述多个独立场景

**错误示例**:
```
A girl in a red dress dancing in a ballroom, then she is wearing a blue dress swimming in a pool
```

**问题**:
- 两个场景完全不相关（舞厅 vs 泳池）
- 服装突变（红裙 vs 蓝裙）
- 模型无法生成连贯过渡

**正确做法**: 描述单一连贯的动作序列

---

### 错误3: 过度详细的描述

**错误示例**:
```
A beautiful young woman with long flowing blonde hair, wearing a red silk dress with intricate lace patterns, standing in a Victorian-era ballroom with crystal chandeliers, marble floors, gold-framed paintings on the walls, slowly walking towards the camera while her dress flows elegantly in the wind, her blue eyes sparkling in the candlelight, her lips curved in a gentle smile...
```

**问题**:
- Prompt 过长（> 500 字符）
- 细节过多，模型难以同时满足所有要求
- 可能导致生成质量下降

**正确做法**: 聚焦核心动作和关键元素

---

### 错误4: 矛盾的描述

**错误示例**:
```
A girl standing still while running fast
```

**问题**:
- "standing still" 和 "running fast" 互相矛盾
- 模型无法同时满足两个冲突的要求

**正确做法**: 确保描述逻辑一致

---

## ✅ 最佳实践

### 原则1: 描述连贯的动作序列

**好的示例**:
```
A girl slowly walking towards the camera, her hair flowing in the wind, maintaining eye contact throughout
```

**为什么好**:
- ✅ 单一连贯的动作（walking）
- ✅ 自然的细节（hair flowing）
- ✅ 持续的状态（maintaining eye contact）

---

### 原则2: 使用动作动词

**好的示例**:
```
A dancer spinning gracefully, her dress twirling around her
```

**动作动词列表**:
- 移动: walking, running, dancing, spinning, jumping
- 手势: waving, pointing, reaching, touching
- 表情: smiling, laughing, looking, gazing
- 身体: bending, stretching, turning, leaning

---

### 原则3: 保持 Prompt 简洁

**推荐长度**: 50-200 字符

**好的示例**:
```
Close-up of a girl smiling and waving at the camera
```

**为什么好**:
- ✅ 简洁明了（10 个单词）
- ✅ 聚焦核心动作
- ✅ 易于模型理解

---

### 原则4: 使用视觉描述，避免抽象概念

**❌ 抽象**:
```
A girl feeling happy and confident
```

**✅ 视觉**:
```
A girl with a bright smile and confident posture
```

**为什么**:
- 模型理解视觉元素（smile, posture）
- 模型难以理解抽象情感（happy, confident）

---

## 📝 Prompt 模板

### 模板1: 人物动作

```
[镜头类型] of [人物描述] [动作] [环境] [细节]
```

**示例**:
```
Close-up shot of a young woman dancing gracefully in a dimly lit room, her hair flowing naturally
```

---

### 模板2: 场景描述

```
[环境] with [人物] [动作], [氛围/光照]
```

**示例**:
```
A sunlit garden with a girl walking among flowers, soft golden hour lighting
```

---

### 模板3: 特写镜头

```
[镜头类型] of [主体] [动作/表情], [背景]
```

**示例**:
```
Extreme close-up of a girl's face smiling gently, blurred background
```

---

## 🎬 不同场景的 Prompt 策略

### 短视频 (< 5秒) - 单一 Prompt

**适用**: 简单动作、单一场景

**示例**:
```
A girl waving at the camera with a bright smile
```

**配置**:
```json
{
  "prompt": "A girl waving at the camera with a bright smile",
  "num_frames": 65,
  "fps": 16
}
```

---

### 中长视频 (5-15秒) - Chain Mode

**适用**: 多个连续动作、场景变化

**示例**:
```
Segment 1: A girl standing in a room, looking at the camera
Segment 2: The girl slowly walking towards the camera
Segment 3: Close-up of the girl smiling
```

**配置**:
```json
{
  "segments": [
    {"prompt": "A girl standing in a room, looking at the camera", "duration": 3.0},
    {"prompt": "The girl slowly walking towards the camera", "duration": 4.0},
    {"prompt": "Close-up of the girl smiling", "duration": 3.0}
  ],
  "auto_continue": true
}
```

---

### 长视频 (> 15秒) - Story Mode

**适用**: 需要保持角色一致性的长视频

**示例**:
```
Segment 1: A girl in a red dress standing in a ballroom
Segment 2: The same girl dancing gracefully
Segment 3: The girl spinning and smiling at the camera
```

**配置**:
```json
{
  "segments": [
    {"prompt": "A girl in a red dress standing in a ballroom", "duration": 5.0},
    {"prompt": "The same girl dancing gracefully", "duration": 5.0},
    {"prompt": "The girl spinning and smiling at the camera", "duration": 5.0}
  ],
  "story_mode": true,
  "auto_continue": true
}
```

**关键**: 在后续 segment 中使用 "the same girl" 来强调身份一致性

---

## 🎨 风格和氛围关键词

### 光照
- `soft lighting` - 柔和光照
- `dramatic lighting` - 戏剧性光照
- `golden hour` - 黄金时段
- `dimly lit` - 昏暗
- `bright sunlight` - 明亮阳光
- `candlelight` - 烛光

### 镜头类型
- `close-up shot` - 特写
- `extreme close-up` - 极特写
- `medium shot` - 中景
- `wide shot` - 远景
- `over-the-shoulder` - 过肩镜头

### 动作速度
- `slowly` - 缓慢
- `gracefully` - 优雅地
- `quickly` - 快速
- `smoothly` - 流畅地
- `gently` - 轻柔地

### 氛围
- `cinematic` - 电影感
- `dreamy` - 梦幻
- `romantic` - 浪漫
- `mysterious` - 神秘
- `energetic` - 充满活力

---

## 🔧 高级技巧

### 技巧1: 使用 Negative Prompt

**用途**: 排除不想要的元素

**示例**:
```
Prompt: A girl dancing in a room
Negative: blurry, distorted, multiple people, text, watermark
```

---

### 技巧2: 强调关键元素

**方法**: 将关键元素放在 prompt 开头

**示例**:
```
✅ 好: Close-up of a girl's smiling face, soft lighting
❌ 差: Soft lighting in a room with a girl whose face is smiling in close-up
```

---

### 技巧3: 使用参考图片 (I2V)

**用途**: 控制起始画面

**步骤**:
1. 上传清晰的参考图片
2. Prompt 描述动作，不需要描述外观
3. 模型会基于图片生成动作

**示例**:
```
参考图片: 一个女孩的照片
Prompt: slowly turning head and smiling at the camera
```

---

### 技巧4: 使用 LoRA 增强风格

**用途**: 添加特定风格或角色

**示例**:
```
Prompt: A girl dancing in anime style
LoRA: anime_style (strength: 0.8)
```

---

## 📊 Prompt 质量检查清单

生成视频前，检查你的 prompt:

- [ ] 是否描述了连贯的动作序列？
- [ ] 是否避免了时间标记 `(at X seconds: ...)`？
- [ ] 是否避免了多个独立场景？
- [ ] Prompt 长度是否合理（50-200 字符）？
- [ ] 是否使用了视觉描述而非抽象概念？
- [ ] 是否有矛盾的描述？
- [ ] 是否使用了动作动词？
- [ ] 如果是多段视频，是否使用了 Chain/Story Mode？

---

## 🎯 实战示例

### 示例1: 简单人物动作

**需求**: 一个女孩挥手

**❌ 错误**:
```
(at 0 seconds: girl standing) (at 2 seconds: girl waving)
```

**✅ 正确**:
```
A girl slowly raising her hand and waving at the camera with a smile
```

---

### 示例2: 复杂场景变化

**需求**: 女孩从站立到跳舞

**❌ 错误**:
```
A girl standing then dancing
```

**✅ 正确** (使用 Chain Mode):
```json
{
  "segments": [
    {"prompt": "A girl standing in a ballroom, looking at the camera", "duration": 2.0},
    {"prompt": "The girl starting to move, swaying to the music", "duration": 2.0},
    {"prompt": "The girl dancing gracefully, spinning around", "duration": 3.0}
  ],
  "auto_continue": true
}
```

---

### 示例3: 保持角色一致性

**需求**: 同一个女孩在不同场景

**❌ 错误**:
```
Segment 1: A blonde girl in a red dress
Segment 2: A girl in a blue dress
```
（角色可能变化）

**✅ 正确** (使用 Story Mode):
```json
{
  "segments": [
    {"prompt": "A girl in a red dress standing in a garden", "duration": 3.0},
    {"prompt": "The same girl walking through the garden", "duration": 3.0},
    {"prompt": "The girl picking flowers and smiling", "duration": 3.0}
  ],
  "story_mode": true
}
```

---

## 📚 参考资源

- **Chain Mode 文档**: `STORY_MODE_SINGLE_SEGMENT.md`
- **Story Mode 快速指南**: `STORY_MODE_QUICK_START.md`
- **API 示例**: `STORY_MODE_API_EXAMPLES.md`
- **场景跳跃问题分析**: `SCENE_JUMPING_ISSUE_ANALYSIS.md`

---

**更新日期**: 2026-03-03
**版本**: 1.0
