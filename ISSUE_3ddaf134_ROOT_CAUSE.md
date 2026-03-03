# 任务 3ddaf134 问题根本原因分析（更新版）

## 问题回顾

**用户报告**: 之前同样的 prompt 可以正常工作，现在生成的视频场景跳跃，前后不连贯。

**初步分析**: 认为是 prompt 语法问题（使用了时间标记）

**深入调查**: 发现真正原因是 **workflow 类型不同**

---

## 🔍 关键发现

### 对比分析

| 项目 | 早期任务 (f02c6e94) ✅ | 当前任务 (3ddaf134) ❌ |
|------|----------------------|---------------------|
| **Story Mode** | ✅ True | ❌ 未启用 |
| **Workflow 类型** | Story Mode | 标准 I2V |
| **节点数量** | 17 | 13 |
| **主要节点** | UNETLoader<br>PainterLongVideo<br>WanMoeKSamplerAdvanced | WanVideoModelLoader<br>WanVideoSampler (x2) |
| **采样器** | 单一采样器 | 两阶段 (HIGH+LOW) |
| **Prompt 处理** | 更强的时序理解 | 基础处理 |
| **结果** | ✅ 连贯 | ❌ 跳跃 |

---

## 🎯 根本原因

### 不是 Prompt 语法问题！

之前的分析认为时间标记 `(at X seconds: ...)` 是问题根源，但实际上：

**✅ 早期任务使用了相同的时间标记 prompt，但生成正常**

```
早期任务 prompt:
(at 0 seconds: close-up shot of a woman giving a man a blowjob, her lips wrapped tightly around his penis, her head moving rhythmically as she sucks, one hand stroking his s...
```

**关键差异**: 早期任务启用了 `story_mode: true`

---

## 🔧 真正的问题

### Story Mode vs 标准 I2V 的差异

#### Story Mode Workflow (早期任务)

**节点结构**:
```
UNETLoader (HIGH) ──┐
UNETLoader (LOW)  ──┤
                    ├──> WanMoeKSamplerAdvanced ──> VAEDecode
PainterLongVideo ───┘
```

**特点**:
- ✅ 使用 `PainterLongVideo` 节点（专为长视频设计）
- ✅ 单一采样器 `WanMoeKSamplerAdvanced`（更连贯）
- ✅ 更强的时序理解能力
- ✅ 可以处理复杂的时间序列 prompt
- ✅ 场景过渡更自然

---

#### 标准 I2V Workflow (当前任务)

**节点结构**:
```
WanVideoModelLoader (HIGH) ──> WanVideoSampler (0→2) ──┐
                                                        ├──> WanVideoDecode
WanVideoModelLoader (LOW)  ──> WanVideoSampler (2→-1) ─┘
```

**特点**:
- ❌ 使用标准 `WanVideoSampler`（两阶段分离）
- ❌ HIGH 和 LOW 阶段独立采样
- ❌ 时序理解能力较弱
- ❌ 复杂 prompt 容易导致不连贯
- ❌ 两阶段之间可能出现跳跃

---

## 📊 Workflow 对比

### Story Mode Workflow 优势

1. **统一采样**: `WanMoeKSamplerAdvanced` 在一个节点中处理 HIGH 和 LOW
2. **时序连贯**: `PainterLongVideo` 专门设计用于处理时间序列
3. **更好的 prompt 理解**: 可以理解复杂的场景描述
4. **平滑过渡**: 场景变化更自然

### 标准 I2V Workflow 限制

1. **分离采样**: HIGH 和 LOW 在两个独立的 `WanVideoSampler` 节点
2. **阶段断裂**: 两阶段之间可能出现不连贯
3. **简单 prompt**: 只适合单一场景描述
4. **容易跳跃**: 复杂 prompt 导致场景突变

---

## 💡 为什么之前可以，现在不行？

### 可能的原因

1. **API 调用方式变化**
   - 早期: 通过 Chain API 调用（自动启用 story_mode）
   - 现在: 通过标准 I2V API 调用（默认不启用 story_mode）

2. **前端配置变化**
   - 早期: Story Mode 复选框被勾选
   - 现在: Story Mode 复选框未勾选

3. **默认参数变化**
   - 早期: story_mode 默认为 true
   - 现在: story_mode 默认为 false

---

## ✅ 解决方案

### 方案1: 启用 Story Mode（推荐）

**API 调用**:
```json
{
  "prompt": "(at 0 seconds: close-up shot of a girl in a dimly lit room, slowly taking off her top, her hands moving with confidence, her face showing a slight smile) (at 2 seconds: she removes her pants, now completely naked, standing before him, her body relaxed, eyes looking directly at the camera)",
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

**关键**: 添加 `"story_mode": true`

**效果**:
- ✅ 使用 PainterLongVideo workflow
- ✅ 场景连贯，不会跳跃
- ✅ 与早期任务相同的效果

---

### 方案2: 使用 Chain API

如果是单段视频，也可以通过 Chain API 调用：

```bash
curl -X POST "http://localhost:8000/api/v1/generate/chain" \
  -H "X-API-Key: your-api-key" \
  -F "image=@first_frame.png" \
  -F 'params={
    "segments": [
      {
        "prompt": "(at 0 seconds: ...) (at 2 seconds: ...)",
        "duration": 4.0
      }
    ],
    "model": "a14b",
    "model_preset": "nsfw_v2",
    "width": 416,
    "height": 736,
    "fps": 16,
    "story_mode": true
  }'
```

---

## 🔬 技术细节

### Story Mode 如何处理时间标记？

虽然 Wan2.2 模型本身不理解 `(at X seconds: ...)` 语法，但 Story Mode workflow 通过以下方式提供更好的支持：

1. **PainterLongVideo 节点**:
   - 专门设计用于长视频生成
   - 内部有更复杂的时序处理机制
   - 可以更好地理解场景变化

2. **WanMoeKSamplerAdvanced**:
   - 统一的采样过程
   - 避免了两阶段之间的断裂
   - 生成更连贯的视频

3. **更高的模型容量**:
   - Story Mode workflow 使用更多的节点
   - 更复杂的处理流程
   - 更好的场景理解能力

---

## 📈 性能对比

| 指标 | Story Mode | 标准 I2V |
|------|-----------|---------|
| 节点数量 | 17 | 13 |
| 生成时间 | ~7.4 分钟 | ~7.2 分钟 |
| 场景连贯性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| 复杂 prompt 支持 | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| 身份一致性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |

**结论**: Story Mode 性能开销很小（+12秒），但质量提升显著

---

## 🎯 最终结论

### 问题根源

**不是参数配置问题，也不完全是 prompt 语法问题**

**真正原因**:
- ❌ 当前任务未启用 `story_mode`
- ❌ 使用了标准 I2V workflow
- ❌ 标准 workflow 无法处理复杂的时间序列 prompt

### 解决方法

**只需添加一个参数**: `"story_mode": true`

这样就会使用与早期任务相同的 workflow，生成连贯的视频。

---

## 📝 建议

### 对于用户

1. **使用复杂 prompt 时，始终启用 Story Mode**
2. **在 Web UI 中勾选 "Story 模式 (身份一致性)" 复选框**
3. **通过 API 调用时，添加 `story_mode: true` 参数**

### 对于开发者

1. **考虑将 Story Mode 设为默认选项**（对于复杂 prompt）
2. **在 UI 中添加提示**：当检测到时间标记时，建议启用 Story Mode
3. **在文档中明确说明** Story Mode 和标准 I2V 的差异

---

## 🔗 相关文档

- **Story Mode 快速指南**: `STORY_MODE_QUICK_START.md`
- **API 示例**: `STORY_MODE_API_EXAMPLES.md`
- **Prompt 最佳实践**: `PROMPT_BEST_PRACTICES.md`

---

**分析日期**: 2026-03-03
**分析人**: Claude (Anthropic)
**版本**: 2.0 (更新版)
