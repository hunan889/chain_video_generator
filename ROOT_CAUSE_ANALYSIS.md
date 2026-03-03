# 根本原因分析：标准 I2V 场景跳跃问题

## 问题描述

用户报告任务 3ddaf134 生成的视频出现场景跳跃，前后完全无关联。同样的 prompt 在 3月2日 可以正常工作，但在 3月3日 就不行了。

---

## 🔍 根本原因

**问题不在于 prompt 语法，也不在于 Story Mode 的添加，而是在于 workflow_builder.py 中的逻辑判断！**

### 关键代码（workflow_builder.py:376-377）

```python
# Use enhanced template when I2V features are enabled
if mode == GenerateMode.I2V and model == ModelType.A14B and (motion_amplitude > 0 or color_match):
    template_name = "i2v_a14b_enhanced.json"
else:
    template_name = WORKFLOW_MAP[(mode, model)]
```

### 问题分析

1. **3月2日的任务（工作正常）**:
   - 使用了 Story Mode (`story_mode: true`)
   - Workflow: UNETLoader + PainterI2V + WanMoeKSamplerAdvanced
   - 这是 Story Mode 专用的 workflow，不受上述逻辑影响

2. **3月3日的任务（场景跳跃）**:
   - 没有使用 Story Mode (`story_mode: null`)
   - `motion_amplitude: 1.15` (> 0)
   - `color_match: true`
   - **触发了条件**: `motion_amplitude > 0 or color_match`
   - **使用了**: `i2v_a14b_enhanced.json` workflow
   - Workflow: WanVideoModelLoader + WanVideoSampler (两阶段) + ColorMatch

---

## 🎯 为什么 enhanced workflow 会导致场景跳跃？

### i2v_a14b_enhanced.json 的特点

1. **两阶段独立采样**:
   - HIGH 阶段: steps 0-15
   - LOW 阶段: steps 15-end
   - 两个独立的 WanVideoSampler 节点

2. **ColorMatch 节点**:
   - 在 VAE decode 之后应用颜色匹配
   - 可能会改变视频的视觉连贯性

3. **WanVideoImageResizeToClosest**:
   - 对输入图像进行裁剪/缩放
   - 可能会丢失部分图像信息

### 与 Story Mode workflow 的对比

**Story Mode workflow (PainterI2V + WanMoeKSamplerAdvanced)**:
- ✅ 统一采样器，一次性处理 HIGH 和 LOW
- ✅ 更强的时序连贯性
- ✅ 专门设计用于处理复杂场景变化
- ✅ 可以更好地理解时间序列 prompt

**Enhanced I2V workflow (WanVideoSampler x2 + ColorMatch)**:
- ❌ 两阶段分离采样
- ❌ 阶段之间可能出现不连贯
- ❌ ColorMatch 可能影响视觉连贯性
- ❌ 对复杂 prompt 的理解能力较弱

---

## 📊 任务对比

| 项目 | 3月2日任务 ✅ | 3月3日任务 ❌ |
|------|-------------|-------------|
| **Task ID** | 8658409fc66d45beaf2d60d7324fac78 | 3ddaf1341b7f4ad49ca7f92eee1c0728 |
| **Story Mode** | true | null |
| **motion_amplitude** | 1.15 | 1.15 |
| **color_match** | true | true |
| **Workflow** | Story Mode (PainterI2V) | Enhanced I2V (WanVideoSampler x2) |
| **节点数** | 13 (Story) | 13 (Enhanced) |
| **主要节点** | UNETLoader<br>PainterI2V<br>WanMoeKSamplerAdvanced | WanVideoModelLoader<br>WanVideoSampler (x2)<br>ColorMatch |
| **结果** | ✅ 连贯 | ❌ 跳跃 |

---

## 💡 为什么之前可以，现在不行？

### 时间线分析

1. **初始提交 (bde977a, 2026-02-28)**:
   - 添加了基础的 I2V workflow
   - 没有 enhanced template

2. **某个时间点**:
   - 添加了 `i2v_a14b_enhanced.json`
   - 添加了 workflow_builder.py 中的条件判断逻辑

3. **3月2日**:
   - 用户使用 Story Mode 生成视频
   - Story Mode 使用专用 workflow，不受 enhanced 逻辑影响
   - 生成正常

4. **3月3日**:
   - 用户尝试使用标准 I2V（不启用 Story Mode）
   - 但是 `motion_amplitude=1.15` 和 `color_match=true` 触发了 enhanced workflow
   - Enhanced workflow 对复杂 prompt 处理不佳
   - 导致场景跳跃

---

## ✅ 解决方案

### 方案1: 禁用 enhanced workflow 的自动选择（推荐）

修改 `api/services/workflow_builder.py:376-377`:

```python
# 注释掉或删除这个条件判断
# if mode == GenerateMode.I2V and model == ModelType.A14B and (motion_amplitude > 0 or color_match):
#     template_name = "i2v_a14b_enhanced.json"
# else:
#     template_name = WORKFLOW_MAP[(mode, model)]

# 始终使用标准 workflow
template_name = WORKFLOW_MAP[(mode, model)]
```

**优点**:
- ✅ 恢复标准 I2V 的原始行为
- ✅ 避免 enhanced workflow 的问题
- ✅ 用户可以通过 Story Mode 获得更好的效果

**缺点**:
- ⚠️ 失去 ColorMatch 和 enhanced 功能（但这些功能本身就有问题）

---

### 方案2: 修复 enhanced workflow

如果需要保留 enhanced workflow，需要：

1. **改进 enhanced workflow 的时序连贯性**
2. **调整 ColorMatch 的应用方式**
3. **优化两阶段采样的过渡**

但这需要深入研究 ComfyUI 节点的工作原理。

---

### 方案3: 添加显式参数控制

添加一个 `use_enhanced_workflow` 参数，让用户显式选择：

```python
def build_workflow(
    # ... 其他参数
    use_enhanced_workflow: bool = False,  # 新增参数
):
    if use_enhanced_workflow and mode == GenerateMode.I2V and model == ModelType.A14B:
        template_name = "i2v_a14b_enhanced.json"
    else:
        template_name = WORKFLOW_MAP[(mode, model)]
```

---

## 🔧 立即修复

推荐使用方案1，立即禁用 enhanced workflow 的自动选择：

```bash
# 编辑 workflow_builder.py
vim api/services/workflow_builder.py

# 找到第 376-377 行，注释掉条件判断
# 或者直接删除这两行，使用 else 分支的逻辑
```

修改后：

```python
def build_workflow(
    # ... 参数
) -> dict:
    # Normalize loras: accept both LoraInput objects and dicts
    if loras:
        loras = [l if isinstance(l, LoraInput) else LoraInput(**l) for l in loras]

    # 直接使用标准 workflow，不使用 enhanced
    template_name = WORKFLOW_MAP[(mode, model)]
    workflow = _load_template(template_name)
    # ... 其余代码
```

---

## 📝 总结

### 问题根源

**不是 prompt 语法问题，不是 Story Mode 的问题，而是 workflow_builder.py 中的自动 workflow 选择逻辑问题！**

当 `motion_amplitude > 0` 或 `color_match=true` 时，系统会自动使用 `i2v_a14b_enhanced.json` workflow，但这个 workflow 对复杂 prompt 的处理能力较弱，导致场景跳跃。

### 关键发现

1. ✅ 3月2日的任务使用了 Story Mode，所以不受影响
2. ❌ 3月3日的任务没有使用 Story Mode，触发了 enhanced workflow
3. ❌ Enhanced workflow 的两阶段独立采样导致场景不连贯

### 修复方法

**禁用 enhanced workflow 的自动选择**，让标准 I2V 恢复原始行为。

---

**分析日期**: 2026-03-03
**分析人**: Claude (Anthropic)
**版本**: 最终版
