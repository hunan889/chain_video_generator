# Story Mode 性能优化方案

> 分析日期: 2026-03-06
> 当前基准: 4段 Story（81帧/段, 5步, 480x832）约 550-590s

---

## 当前工作流耗时分布

| 阶段 | 节点数 | 预估耗时 | 占比 |
|------|--------|----------|------|
| 模型加载（HIGH+LOW+VAE+CLIP+CLIPVision） | 6 | 50-70s（首次,缓存后~0s） | ~12% |
| PathchSageAttention + ModelPatchTorchSettings | 4 | ~2s | <1% |
| **采样（4段 x 5步 x HIGH/LOW模型）** | **4** | **~400s** | **~68%** |
| VRAMCleanup（8个: pre+post 各4） | 8 | ~16s | ~3% |
| PainterI2V/LongVideo 编码 | 4 | ~20-30s | ~5% |
| VAE 解码（4段） | 4 | ~40-60s | ~8% |
| ColorMatch + ImageBatchMulti | 4 | ~3s | <1% |
| VHS_VideoCombine（ffmpeg 编码） | 1 | ~10s | ~2% |
| 工具节点（mxSlider 等） | 6 | ~1s | <1% |
| 空 LoRA Loader（无 LoRA 时） | 8 | ~4s | <1% |

**结论: 采样占 68%，是绝对瓶颈。**

---

## 方案1: 启用 torch.compile（低复杂度, 预计节省 60-120s）

### 现状

`PathchSageAttentionKJ` 节点的 `allow_compile` 设为 `False`:

```python
# workflow_builder.py line 1089, 1099
"allow_compile": False,
```

### 优化

改为 `True`，让 PyTorch 编译 attention 计算图:

```python
"allow_compile": True,
```

### 效果

- 首次编译需 60-120s（一次性开销，模型缓存后不再重复）
- 之后每次 forward pass 加速 15-30%
- Story 模式多段共享模型，编译成本在第一段就能回收
- 预计采样阶段节省 60-120s

### 风险

- 某些 ComfyUI 操作可能与 compile 不兼容
- 需要测试确认无报错

### 涉及文件

- `api/services/workflow_builder.py` — `build_merged_story_workflow()` 中的 PathchSageAttentionKJ 节点

---

## 方案2: 精简冗余节点（低复杂度, 预计节省 15-20s）

### 2a. 去掉工具节点, 用直接值替代

当前有 6 个节点只做值传递:

| 节点 | 类型 | 作用 | 替代 |
|------|------|------|------|
| 1282 | mxSlider | 传递 num_frames | 直接传整数 |
| 1283 | mxSlider | 传递 steps | 直接传整数 |
| 604 | FloatConstant | 传递 motion_amplitude | 直接传浮点数 |
| 605 | INTConstant | 传递 motion_frames | 直接传整数 |
| 1551 | PrimitiveFloat | 传递 sigma_shift | 直接传浮点数 |
| 1480/1481 | SamplerSelector/SchedulerSelector | 传递字符串 | 直接传字符串 |

改动: 删除这些节点，所有引用改为直接值。例如:

```python
# 之前
"length": ["1282", 0],
"steps": ["1283", 0],
"motion_amplitude": ["604", 0],

# 之后
"length": num_frames,
"steps": steps,
"motion_amplitude": motion_amplitude,
```

**预计节省: ~1-2s**

### 2b. 无 LoRA 时跳过 Power Lora Loader

当前无论有没有 LoRA，每段都创建 2 个空 Power Lora Loader（HIGH + LOW）。

改动: 当 `loras` 为空时，sampler 直接连接 ModelPatchTorchSettings 输出:

```python
if loras:
    model_high_ref = [ids["lora_high"], 0]
    model_low_ref = [ids["lora_low"], 0]
else:
    model_high_ref = ["1252:1279", 0]  # ModelPatchTorchSettings HIGH
    model_low_ref = ["1252:1280", 0]   # ModelPatchTorchSettings LOW
```

**预计节省: ~2-4s（每段少 2 个节点）**

### 2c. 减少 VRAMCleanup 数量

当前每段 2 个 VRAMCleanup:
- `s{i}_pre_vram`: sampler 前（offload_model + offload_cache）
- `s{i}_vram`: sampler 后, VAE decode 前

`offload_model=True` 每次把模型从 GPU 卸载到 CPU 再加载回来，开销 1-2s/次。

优化策略:
- 去掉 `pre_vram`（模型刚用完 sampler，不需要先卸载再加载）
- 保留 `post_vram`（sampler 后释放 VRAM 给 VAE decode）
- 或改为只做 `offload_cache=True`（清缓存但不卸载模型）

**预计节省: ~8-12s（4段少 4 个 VRAMCleanup）**

### 涉及文件

- `api/services/workflow_builder.py` — `build_merged_story_workflow()`

---

## 方案3: TeaCache 步骤缓存（中复杂度, 预计节省 60-80s）

### 原理

TeaCache 在扩散模型的 forward 中检测相邻步骤的输出差异（L1距离），
差异小于阈值时跳过整步计算，复用缓存结果。

### 现状

- WanVideoWrapper transformer 已内置 TeaCache 逻辑
  (`wanvideo/modules/model.py:2978`, 检查 `self.enable_teacache`)
- 但 `WanMoeKSamplerAdvanced`（我们的采样器）**不支持 cache_args 输入**
- WanVideoWrapper 的 `WanVideoSampler` 支持，但接口不兼容
  （使用 `WANVIDEOMODEL` 而非标准 `MODEL` 类型）

### 实现路径

修改 `ComfyUI-WanMoeKSampler/nodes.py`，给 `WanMoeKSamplerAdvanced` 添加 TeaCache 支持:

```python
class WanMoeKSamplerAdvanced:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            # ... 现有参数 ...
        },
        "optional": {
            "cache_args": ("CACHEARGS", ),  # 新增
        }}

    def sample(self, ..., cache_args=None):
        model_high_noise = set_shift(model_high_noise, sigma_shift)
        model_low_noise = set_shift(model_low_noise, sigma_shift)

        # 在采样前设置 TeaCache 属性
        if cache_args and cache_args.get("cache_type") == "TeaCache":
            for model in [model_high_noise, model_low_noise]:
                transformer = model.model.diffusion_model
                transformer.enable_teacache = True
                transformer.rel_l1_thresh = cache_args["rel_l1_thresh"]
                transformer.teacache_start_step = cache_args["start_step"]
                # end_step 需要根据实际步数计算
                transformer.teacache_use_coefficients = cache_args["use_coefficients"]

        result = wan_ksampler(...)

        # 采样后清理
        if cache_args:
            for model in [model_high_noise, model_low_noise]:
                model.model.diffusion_model.enable_teacache = False

        return result
```

在 workflow builder 中添加 TeaCache 节点:

```python
workflow["teacache"] = {
    "class_type": "WanVideoTeaCache",
    "inputs": {
        "rel_l1_thresh": 0.2,       # A14B 推荐 0.15-0.30
        "start_step": 1,             # 跳过第一步
        "end_step": -1,              # 到最后
        "cache_device": "offload_device",
        "use_coefficients": True,
    },
}
# 连接到每段的 sampler
workflow[ids["sampler"]]["inputs"]["cache_args"] = ["teacache", 0]
```

### 效果评估

- 5 步: 约 15-20% 采样加速（步数少，可跳过空间有限）
- 10 步: 约 30-40% 采样加速
- 20 步: 约 40-50% 采样加速（效果最显著）

### 注意事项

- 阈值 `rel_l1_thresh` 过高会导致画面伪影
- HIGH/LOW 模型各有独立的 transformer，需要分别设置
- `teacache_end_step` 依赖总步数，需在 sampler 内部计算
- 此修改需改动第三方节点 `ComfyUI-WanMoeKSampler`

### 涉及文件

- `ComfyUI/custom_nodes/ComfyUI-WanMoeKSampler/nodes.py` — 添加 cache_args 支持
- `api/services/workflow_builder.py` — 添加 WanVideoTeaCache 节点到工作流

---

## 方案4: MultiGPU 模型分布（高复杂度, 预计节省 150-200s）

### 现状

- 8 x RTX 4090，GPU 0-2 分配给 Wan2.2，3-7 给其他服务
- ComfyUI 当前单 GPU 推理
- `ComfyUI-MultiGPU` 已安装，提供 `UNETLoaderMultiGPU` 等节点

### 思路

用 `UNETLoaderMultiGPU` 替代 `UNETLoader`，将 14B 模型的 transformer blocks
拆分到 2-3 块 GPU 上做 tensor parallel 推理:

```python
# 替换
workflow["917"] = {
    "class_type": "UNETLoaderMultiGPU",  # 替代 UNETLoader
    "inputs": {
        "unet_name": model_info["high"],
        "weight_dtype": "default",
    },
}
```

### 难点

1. MultiGPU 主要适配 WanVideoWrapper 原生节点（`WanVideoSamplerMultiGPU`），
   不确定是否兼容 `WanMoeKSamplerAdvanced` 的 MODEL 类型
2. 可能需要切换整个采样管线到 WanVideoWrapper 原生节点
3. 需要重新分配 GPU（当前 GPU 3-7 被其他服务占用）
4. DisTorch2 模式需要额外配置

### 替代思路

如果 MultiGPU 不兼容 WanMoeKSampler，可以考虑:
- 放弃 MoE 双模型架构，用单个 WanVideoSampler + MultiGPU
- 或用 DisTorch2 模式的 UNETLoader 做跨 GPU 张量并行

### 预期收益

- 2 GPU: 采样速度约 1.5-1.8x（不是线性，有通信开销）
- 3 GPU: 采样速度约 2.0-2.5x
- 理论上 400s 采样 → 160-200s

### 涉及文件

- `api/services/workflow_builder.py` — 替换 Loader 节点类型
- ComfyUI 启动参数可能需要调整

---

## 方案5: 优化采样策略（低复杂度, 预计节省 5-15s）

### 5a. 调整 boundary 参数

当前 `boundary=0.9`，5步分配:
- HIGH 模型: 1 步（step 0, timestep ~1.0）
- LOW 模型: 4 步（steps 1-4）

如果 LOW 模型更轻量，可测试:
- `boundary=0.85`: 可能让 HIGH 跑 0 步（全部 LOW）
- 需验证画质影响

### 5b. 减少 motion_frames

当前 `motion_frames=5`，PainterLongVideo 取上一段最后 5 帧做运动参考。
减到 3 帧可略微减少编码计算（影响较小）。

---

## 实施优先级

| 优先级 | 方案 | 预计节省 | 复杂度 | 风险 | 状态 |
|--------|------|----------|--------|------|------|
| 1 | torch.compile | 60-120s | 低（改1行） | 中 | 待实施 |
| 2 | 精简节点 + 减 VRAMCleanup | 15-20s | 低 | 低 | 待实施 |
| 3 | TeaCache 集成 | 60-80s | 中 | 中 | 待实施 |
| 4 | MultiGPU | 150-200s | 高 | 高 | 待评估 |
| 5 | 采样策略调优 | 5-15s | 低 | 低 | 待测试 |

**方案1+2 可立即实施（预计节省 80-140s，风险低）**
**方案3 需修改第三方节点（预计额外节省 60-80s）**
**方案4 需架构评估（潜在收益最大但改动最大）**

---

## 附: 当前 Merged Story 工作流数据流图

```
LoadImage(97) --> CLIPVisionEncode(cv_encode) ----+
                                                   |
UNETLoader(917/918) --> SageAttention --> TorchSettings --> [per-seg LoRA] --+
                                                                             |
CLIPLoader(1521) --> CLIPTextEncode(pos/neg) --------+                       |
                                                     |                       |
                                               PainterI2V/LongVideo          |
                                                     |                       |
                                               WanMoeKSamplerAdvanced <------+
                                                     |
                                               VRAMCleanup
                                                     |
                                                 VAEDecode
                                                     |
                                              ImageBatchMulti (合并帧)
                                                     |
                                                ColorMatch
                                                     |
                                            [可选: Upscale/RIFE/MMAudio]
                                                     |
                                              VHS_VideoCombine --> 输出.mp4
```
