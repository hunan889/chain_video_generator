# 任务 3ddaf134 问题总结

## 任务信息
- **任务ID**: 3ddaf1341b7f4ad49ca7f92eee1c0728
- **状态**: 已完成
- **问题**: 生成的视频前后场景完全无关联，直接跳场景

---

## 问题分析

### 用户报告的问题
✅ 参数配置正确
❌ **生成的视频不符合预期，场景直接跳跃**

### 根本原因

**Prompt 使用了错误的语法**:
```
(at 0 seconds: close-up shot of a girl in a dimly lit room, slowly taking off her top, her hands moving with confidence, her face showing a slight smile) (at 2 seconds: she removes her pants, now completely naked, standing before him, her body relaxed, eyes looking directly at the camera)
```

**问题**:
1. ❌ Wan2.2 模型不支持 `(at X seconds: ...)` 时间标记语法
2. ❌ 单个 prompt 包含两个独立的场景描述
3. ❌ 模型将整个 prompt 同时应用到所有帧，导致场景混乱

---

## 参数验证结果

### ✅ 所有技术参数都正确

| 参数 | 值 | 状态 |
|------|-----|------|
| 分辨率 | 416x736 | ✅ 符合 16 倍数 |
| 帧数 | 65 | ✅ 符合 4n+1 |
| FPS | 16 | ✅ 正常 |
| Steps | 4 (强制) | ✅ nsfw_v2 预期行为 |
| CFG | 1.0 | ✅ 推荐值 |
| Shift | 8.0 | ✅ 正常 |
| Scheduler | euler | ✅ 推荐值 |
| 模型预设 | nsfw_v2 | ✅ 正常 |

### Steps 参数说明

用户请求 `steps: 20`，实际执行 `steps: 4`

**这不是 bug，是预期行为**:
- nsfw_v2 是 FP8 优化模型，专门设计为 4 步即可达到高质量
- 代码会强制覆盖用户的 steps 参数
- 4 步分配: HIGH (0→2) + LOW (2→-1)

---

## 解决方案

### 方案1: 使用单一连贯的 Prompt

**适用**: 简单短视频

**修改后的 Prompt**:
```
close-up shot of a girl in a dimly lit room, slowly undressing with smooth continuous motion, maintaining confident eye contact with the camera throughout
```

**优点**:
- ✅ 描述连贯动作
- ✅ 避免场景跳跃
- ✅ 简单直接

---

### 方案2: 使用 Chain Mode (推荐)

**适用**: 需要多个场景的视频

**配置**:
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
- ✅ 每个 segment 独立 prompt
- ✅ VLM 自动生成连贯过渡
- ✅ 场景变化自然

---

### 方案3: 使用 Story Mode (最佳)

**适用**: 需要保持角色一致性

**配置**:
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
- ✅ 保持角色身份一致
- ✅ 场景过渡自然
- ✅ 最佳视频质量

**注意**: 需要上传首帧图片作为身份参考

---

## API 调用示例

### Chain Mode

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

---

## 关键要点

### ❌ 不要做
1. 不要在单个 prompt 中使用时间标记 `(at X seconds: ...)`
2. 不要在单个 prompt 中描述多个独立场景
3. 不要期望模型理解时间条件

### ✅ 应该做
1. 使用单一连贯的动作描述（短视频）
2. 使用 Chain Mode 分段生成（多场景）
3. 使用 Story Mode 保持身份一致（长视频）
4. 在 prompt 中使用 "the same girl" 强调一致性

---

## 相关文档

- **场景跳跃详细分析**: `SCENE_JUMPING_ISSUE_ANALYSIS.md`
- **Prompt 最佳实践**: `PROMPT_BEST_PRACTICES.md`
- **Story Mode 快速指南**: `STORY_MODE_QUICK_START.md`
- **API 示例**: `STORY_MODE_API_EXAMPLES.md`

---

## 总结

**问题**: 不是参数配置问题，而是 **Prompt 语法问题**

**原因**: Wan2.2 模型不支持时间标记语法

**解决**: 使用 Chain Mode 或 Story Mode 分段生成

**建议**: 使用 Story Mode + 首帧图片，获得最佳效果

---

**分析日期**: 2026-03-03
**分析人**: Claude (Anthropic)
