# 视频花屏问题分析

## 问题描述

任务 be51c1e8c00747a898b88a0b2d1d105c 生成的视频出现花屏现象。

---

## 🔍 根本原因

### 错误的参数设置

在 `workflow_builder.py` 第 445-446 行：

```python
if "augment_empty_frames" in inputs:
    inputs["augment_empty_frames"] = motion_amplitude
```

**问题**:
1. `augment_empty_frames` 是一个实验性参数，用于增强空帧的运动
2. 默认值: 0.0，推荐范围: 0.0-0.3
3. 但代码将其设置为 `motion_amplitude` (1.15)，**过大导致花屏**

### 参数混淆

- **`motion_amplitude`**: PainterI2V 节点的参数，控制整体运动幅度，范围 0.0-2.0
- **`augment_empty_frames`**: WanVideoImageToVideoEncode 节点的参数，控制空帧增强，范围 0.0-10.0（但实际应该 < 0.5）

**这两个参数完全不同，不应该混用！**

---

## 📊 任务详情

**任务 ID**: be51c1e8c00747a898b88a0b2d1d105c

**完成时间**: 2026-03-03 09:16:26 UTC（修复前）

**参数**:
- `motion_amplitude`: 1.15
- `augment_empty_frames`: 1.15（错误！应该是 0.15）

**Workflow**: i2v_a14b_enhanced.json

**节点 7 (WanVideoImageToVideoEncode)**:
```json
{
  "inputs": {
    "width": 416,
    "height": 736,
    "num_frames": 65,
    "noise_aug_strength": 0.05,
    "augment_empty_frames": 1.15,  // ❌ 错误！过大导致花屏
    "start_latent_strength": 1.0,
    "end_latent_strength": 1.0
  }
}
```

---

## 🎯 为什么会花屏？

### augment_empty_frames 的工作原理

根据 ComfyUI-WanVideoWrapper 源码：

```python
if augment_empty_frames > 0.0:
    y = y[:, :1] + (y - y[:, :1]) * ((augment_empty_frames+1) * frame_is_empty + ~frame_is_empty)
```

**正常情况** (augment_empty_frames = 0.15):
- 空帧增强系数: (0.15 + 1) = 1.15
- 运动差异被适度放大

**错误情况** (augment_empty_frames = 1.15):
- 空帧增强系数: (1.15 + 1) = 2.15
- 运动差异被过度放大，**超出 VAE 的有效范围**
- 导致解码时出现花屏、噪点、伪影

---

## ✅ 解决方案

### 已实施的修复

**禁用 enhanced workflow**（commit 079d8a6）:
- 移除了 enhanced workflow 的自动选择逻辑
- 始终使用标准 workflow (i2v_a14b.json)
- 标准 workflow 没有 `augment_empty_frames` 参数，避免了这个问题

### 如果需要保留 enhanced workflow

需要修复 `workflow_builder.py` 第 445-446 行：

```python
# 错误的代码
if "augment_empty_frames" in inputs:
    inputs["augment_empty_frames"] = motion_amplitude  # ❌ 错误！

# 正确的代码
if "augment_empty_frames" in inputs:
    # augment_empty_frames 应该保持模板中的默认值 (0.15)
    # 或者根据 motion_amplitude 计算一个合理的小值
    inputs["augment_empty_frames"] = min(0.3, motion_amplitude * 0.15)  # ✅ 正确
```

---

## 📝 参数对比

| 参数 | 用途 | 节点 | 默认值 | 推荐范围 | 任务中的值 |
|------|------|------|--------|----------|-----------|
| `motion_amplitude` | 控制整体运动幅度 | PainterI2V | 1.0 | 0.5-2.0 | 1.15 ✅ |
| `augment_empty_frames` | 增强空帧运动 | WanVideoImageToVideoEncode | 0.0 | 0.0-0.3 | 1.15 ❌ |

---

## 🔧 验证修复

### 测试步骤

1. 使用相同的参数重新生成视频
2. 确认使用标准 workflow (i2v_a14b.json)
3. 检查生成的视频是否正常

### 预期结果

- ✅ 不再使用 enhanced workflow
- ✅ 不再设置 `augment_empty_frames` 参数
- ✅ 视频正常，无花屏现象

---

## 📚 相关文档

- **根本原因分析**: `ROOT_CAUSE_ANALYSIS.md`
- **Enhanced workflow 问题**: 场景跳跃 + 花屏
- **修复 commit**: 079d8a6

---

## 💡 经验教训

1. **不要混用不同节点的参数**
   - `motion_amplitude` 是 PainterI2V 的参数
   - `augment_empty_frames` 是 WanVideoImageToVideoEncode 的参数
   - 它们的含义和范围完全不同

2. **实验性参数需要谨慎使用**
   - `augment_empty_frames` 标记为 EXPERIMENTAL
   - 过大的值会导致 VAE 解码失败

3. **Enhanced workflow 存在多个问题**
   - 场景跳跃（两阶段独立采样）
   - 花屏（错误的参数设置）
   - 建议使用标准 workflow 或 Story Mode

---

**分析日期**: 2026-03-03
**分析人**: Claude (Anthropic)
**任务 ID**: be51c1e8c00747a898b88a0b2d1d105c
**状态**: ✅ 已修复（通过禁用 enhanced workflow）
