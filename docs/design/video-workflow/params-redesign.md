# 高级工作流参数重新设计

## 问题分析

当前参数配置存在以下问题：
1. 参数没有按照执行阶段组织，混乱
2. 缺少关键参数（首帧换脸、视频换脸、后处理等）
3. SeeDream Prompt 不可编辑，无法自定义
4. T2I CFG Scale 等参数归属不清晰

## 新设计：按阶段组织参数

### Stage 1: 📝 Prompt 分析阶段

**功能开关**
- `auto_analyze`: 是否自动分析 (默认: true)
- `auto_lora`: 是否自动推荐 LORA (默认: true)
- `auto_prompt`: 是否自动优化 Prompt (默认: true)

**推荐配置**
- `top_k_image_loras`: Image LORA 推荐数量 (默认: 5)
- `top_k_video_loras`: Video LORA 推荐数量 (默认: 5)

---

### Stage 2: 🖼️ 首帧获取阶段

**首帧来源**
- `first_frame_source`:
  - `use_uploaded`: 使用上传图片
  - `generate`: T2I 生成
  - `select_existing`: 选择已有图片

**T2I 生成参数**（仅当 source=generate 时生效）
- `t2i_steps`: 采样步数 (默认: 20)
- `t2i_cfg_scale`: CFG Scale (默认: 7.0)
- `t2i_sampler`: 采样器 (默认: "DPM++ 2M Karras")
- `t2i_seed`: 随机种子 (默认: -1)
- `t2i_width`: 宽度 (从用户选择的分辨率计算)
- `t2i_height`: 高度 (从用户选择的分辨率计算)

**首帧换脸**（可选）
- `first_frame_face_swap`: 是否对首帧换脸 (默认: false)
- `first_frame_face_swap_strength`: 换脸强度 (默认: 1.0)

---

### Stage 3: ✨ SeeDream 编辑阶段

**编辑模式**
- `seedream_mode`:
  - `face_only`: 仅面部
  - `face_wearings`: 面部+配饰
  - `full_body`: 全身

**默认 Prompt（根据模式自动填充，可编辑）**
- `face_only`: "edit image 2, keep the position and pose of image 2, swap face to image 1, only change the face, keep everything else exactly the same including clothing, accessories, background"
- `face_wearings`: "edit image 2, keep the position and pose of image 2, swap face to image 1, change face and accessories (jewelry, glasses, hair accessories) to match image 1, keep clothing and background the same"
- `full_body`: "edit image 2, keep the position and pose of image 2, swap face to image 1, change face, accessories, and clothing to match image 1, keep background the same"

**SeeDream 参数**
- `seedream_prompt`: 自定义 Prompt (可编辑，默认根据模式填充)
- `seedream_enable_reactor`: 是否在 SeeDream 前先用 Reactor 换脸 (默认: true)
- `seedream_strength`: 编辑强度 (默认: 0.8)
- `seedream_seed`: 随机种子 (默认: null)

---

### Stage 4: 🎬 视频生成阶段

**视频生成参数**
- `video_model`: A14B / 5B (默认: A14B)
- `video_steps`: 采样步数 (默认: 20)
- `video_cfg`: CFG Scale (默认: 6.0)
- `video_shift`: Shift (默认: 5.0)
- `video_scheduler`: 调度器 (默认: unipc)
- `video_noise_aug_strength`: 噪声增强强度 (默认: 0.0)
- `video_motion_amplitude`: 运动幅度 (默认: 0.0)

**视频换脸**（可选）
- `video_face_swap`: 是否对视频换脸 (默认: false)
- `video_face_swap_mode`: 换脸模式
  - `face_reference`: 仅换脸
  - `full_body_reference`: 全身参考
- `video_face_swap_strength`: 换脸强度 (默认: 1.0)

**后处理参数**
- `enable_upscale`: 是否启用超分 (默认: false)
- `upscale_model`: 超分模型 (默认: "RealESRGAN_x4plus")
- `upscale_resize`: 超分倍数 (默认: 2.0)
- `enable_interpolation`: 是否启用插帧 (默认: false)
- `interpolation_multiplier`: 插帧倍数 (默认: 2)
- `interpolation_profile`: 插帧配置 (默认: "auto")

---

## UI 设计

### 主要参数区（始终可见）
```
提示词: [textarea]
上传图片作用: [首帧 / 全身参考 / 仅换脸]
分辨率: [720p / 1080p]
宽高比: [16:9 / 9:16 / 3:4 / 4:3]
时长: [5s / 10s / 15s]
上传图片: [upload area]
```

### 高级参数区（折叠面板，按阶段组织）

```
⚙️ 高级内部参数配置 ▼

  📝 Stage 1: Prompt 分析
    [折叠面板]
    - 自动分析 Prompt: [启用/禁用]
    - 自动推荐 LORA: [启用/禁用]
    - 自动优化 Prompt: [启用/禁用]
    - Image LORA 推荐数量: [5]
    - Video LORA 推荐数量: [5]

  🖼️ Stage 2: 首帧获取
    [折叠面板]
    - 首帧来源策略: [使用上传/T2I生成/选择已有]

    T2I 生成参数:
    - 采样步数: [20]
    - CFG Scale: [7.0]
    - 采样器: [DPM++ 2M Karras]
    - 随机种子: [-1]

    首帧换脸:
    - 是否换脸: [否]
    - 换脸强度: [1.0]

  ✨ Stage 3: SeeDream 编辑
    [折叠面板]
    - 编辑模式: [仅面部/面部+配饰/全身]
    - SeeDream Prompt: [textarea with default]
    - 编辑前 Reactor 换脸: [启用/禁用]
    - 编辑强度: [0.8]
    - 随机种子: [auto]

  🎬 Stage 4: 视频生成
    [折叠面板]
    视频生成:
    - 模型: [A14B/5B]
    - 采样步数: [20]
    - CFG Scale: [6.0]
    - Shift: [5.0]
    - 调度器: [unipc]
    - 噪声增强: [0.0]
    - 运动幅度: [0.0]

    视频换脸:
    - 是否换脸: [否]
    - 换脸模式: [仅换脸/全身参考]
    - 换脸强度: [1.0]

    后处理:
    - 启用超分: [否]
    - 超分模型: [RealESRGAN_x4plus]
    - 超分倍数: [2.0]
    - 启用插帧: [否]
    - 插帧倍数: [2]
```

---

## API Schema 更新

```python
class WorkflowGenerateRequest(BaseModel):
    # 用户参数
    mode: Literal["face_reference", "full_body_reference", "first_frame"]
    user_prompt: str
    reference_image: Optional[str]

    # 基础配置
    first_frame_source: FirstFrameSource
    uploaded_first_frame: Optional[str]
    selected_image_url: Optional[str]

    auto_analyze: bool = True
    auto_lora: bool = True
    auto_prompt: bool = True

    # 简化的参数（向后兼容）
    t2i_params: Optional[dict]
    seedream_params: Optional[dict]
    video_params: Optional[dict]

    # 新增：按阶段组织的内部配置
    internal_config: Optional[dict] = {
        "stage1_prompt_analysis": {
            "auto_analyze": true,
            "auto_lora": true,
            "auto_prompt": true,
            "top_k_image_loras": 5,
            "top_k_video_loras": 5
        },
        "stage2_first_frame": {
            "first_frame_source": "use_uploaded",
            "t2i": {
                "steps": 20,
                "cfg_scale": 7.0,
                "sampler": "DPM++ 2M Karras",
                "seed": -1
            },
            "face_swap": {
                "enabled": false,
                "strength": 1.0
            }
        },
        "stage3_seedream": {
            "mode": "face_wearings",
            "prompt": "...",
            "enable_reactor": true,
            "strength": 0.8,
            "seed": null
        },
        "stage4_video": {
            "generation": {
                "model": "A14B",
                "steps": 20,
                "cfg": 6.0,
                "shift": 5.0,
                "scheduler": "unipc",
                "noise_aug_strength": 0.0,
                "motion_amplitude": 0.0
            },
            "face_swap": {
                "enabled": false,
                "mode": "face_reference",
                "strength": 1.0
            },
            "postprocess": {
                "upscale": {
                    "enabled": false,
                    "model": "RealESRGAN_x4plus",
                    "resize": 2.0
                },
                "interpolation": {
                    "enabled": false,
                    "multiplier": 2,
                    "profile": "auto"
                }
            }
        }
    }
```

---

## 实现步骤

1. ✅ 创建设计文档
2. ⏳ 更新后端执行逻辑
   - 2.1 添加参数读取辅助函数 `_get_config()`
   - 2.2 添加 SeeDream 默认 prompt 函数
   - 2.3 添加首帧换脸功能
   - 2.4 更新 SeeDream 支持自定义 prompt
   - 2.5 更新视频生成支持换脸和后处理
3. ⏳ 更新 HTML 界面 - 按阶段组织参数
4. ⏳ 添加 SeeDream Prompt 编辑框，根据模式自动填充
5. ⏳ 添加缺失的换脸参数
6. ⏳ 添加后处理参数
7. ⏳ 更新 JavaScript 请求构建逻辑
8. ⏳ 测试所有阶段的参数生效

---

## 后端实现细节

### 文件改动清单

**api/routes/workflow_executor.py** (主要改动)
- [ ] 添加 `_get_config()` - 参数读取辅助函数
- [ ] 添加 `get_default_seedream_prompt()` - 默认 prompt 生成
- [ ] 添加 `_apply_face_swap_to_frame()` - 首帧换脸功能
- [ ] 更新 `_acquire_first_frame()` - 支持首帧换脸
- [ ] 更新 `_edit_first_frame()` - 支持自定义 prompt
- [ ] 更新 `_generate_video()` - 支持视频换脸和后处理

**api/routes/workflow.py** (无需改动)
- [x] Schema 已有 `internal_config` 字段

### 参数读取优先级

```
internal_config[stage][key] > 旧参数字段 > 默认值
```

示例：
```python
# 读取 SeeDream 编辑模式
# 1. 优先从 internal_config.stage3_seedream.mode 读取
# 2. 如果没有，从 seedream_params.edit_mode 读取
# 3. 如果还没有，使用默认值 "face_wearings"
```

### 向后兼容性

- ✅ 旧的 API 调用（不传 internal_config）仍然有效
- ✅ 新的 API 调用（传 internal_config）优先使用新参数
- ✅ 可以混合使用（部分用新参数，部分用旧参数）

---

## 优势

1. **清晰的阶段划分**：每个参数归属明确
2. **完整的参数覆盖**：补充了所有缺失的参数
3. **灵活的换脸控制**：首帧和视频都可以独立控制换脸
4. **可编辑的 Prompt**：SeeDream Prompt 可自定义
5. **完整的后处理**：支持超分和插帧
6. **向后兼容**：保留原有的简化参数字段
