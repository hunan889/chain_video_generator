# Prompt Optimizer - 详细扩展增强版

## 增强目标

在保持用户原意的基础上，添加更多**大尺度细节描述**，让视频生成更加具体和符合预期。

---

## 核心改进

### 1. 扩展提示更加详细

**旧版扩展提示（简单）：**
```python
r'\bblowjob\b': "add details about head movement, hand positioning, eye contact"
```

**新版扩展提示（详细）：**
```python
r'\bblowjob\b': "add explicit details: how deep she takes him, her lips wrapped around
the shaft, tongue movement along the underside, hand stroking the base, saliva dripping,
eye contact looking up, cheeks hollowing, head bobbing rhythm, gagging sounds"
```

### 2. 新增更多术语的详细扩展

新增了以下术语的详细扩展指导：
- `pussy` - 阴部细节（湿润、粉红、阴唇、阴蒂、收缩）
- `cock` - 阴茎细节（粗细、长度、血管、龟头、硬度、前列腺液）
- `penetration` - 插入细节（深度、角度、进出动作、阴唇拉伸、湿润声音）
- `thrusting` - 抽插细节（节奏、深度、角度、身体撞击声）
- `sex` - 性交细节（插入、抽插、体位、接触、湿润、声音、节奏、强度）

### 3. 核心规则强化

**新增的详细扩展要求：**

```
## What to ADD (Detailed Expansion):
2. **Explicit physical details**:
   - Body parts: specific anatomy (cock, pussy, breasts, ass, lips, tongue, etc.)
   - Penetration details: depth, angle, in-and-out motion, stretching, gripping
   - Fluids: wetness, saliva, precum, cum (where it goes, how it looks)
   - Sounds: wet sounds, slapping, moaning, gagging
3. **Motion mechanics**:
   - Speed: slowly, rapidly, rhythmically, gently, forcefully, hard, fast
   - Direction: up/down, in/out, back/forth, side to side
   - Rhythm: steady, increasing, pounding, grinding
   - Body physics: bouncing, jiggling, rippling, rocking
4. **Physical reactions**:
   - Facial: eyes rolling, mouth opening, tongue out, lips parting, cheeks flushing
   - Body: back arching, legs spreading, toes curling, fingers gripping, body shaking
   - Vocalizations: moaning, screaming, gasping
```

### 4. 示例更加详细

**旧版示例（简单）：**
```
Input: "A sexy woman is having sex with a man in the face-down ass-up position"
Output: "(at 0 seconds: A sexy woman is having sex with a man in the face-down ass-up
position, her hips raised high, her upper body pressed down) (at 2 seconds: he thrusts
rhythmically from behind, his hands gripping her waist, her body rocking back and forth)"
```
字数：~50 words

**新版示例（详细）：**
```
Input: "A sexy woman is having sex with a man in the face-down ass-up position"
Output: "(at 0 seconds: A sexy woman is having sex with a man in the face-down ass-up
position, her ass raised high in the air, her upper body pressed flat against the surface,
her pussy fully exposed and wet) (at 2 seconds: his cock thrusts deep into her pussy from
behind, his hands gripping her hips tightly, pulling her back onto him with each stroke,
her ass cheeks rippling with the impact) (at 4 seconds: he pounds into her harder and
faster, his cock sliding in and out of her dripping wet pussy, her body rocking forward
with each thrust, her back arching more, her moans getting louder, wet slapping sounds
filling the air)"
```
字数：~130 words

---

## 实际效果对比

### 测试案例 1: Face-down ass-up position

**输入：**
```
A sexy woman is having sex with a man in the face-down ass-up position
```

**旧版输出（~50 words）：**
```
(at 0 seconds: A sexy woman is having sex with a man in the face-down ass-up position,
her hips raised high, her upper body pressed down against the surface, her back arching
slightly as she moves with each thrust)
```

**新版输出（~97 words）：**
```
(at 0 seconds: A sexy woman is having sex with a man in the face-down ass-up position,
her ass raised high in the air, her upper body pressed flat against the surface, her
pussy fully exposed and wet, his cock penetrating deep into her pussy, the wet sound
of each thrust echoing as he pushes all the way in, her body shivering with every stroke,
her ass cheeks rippling with the motion, her moans growing louder as he continues to
thrust rhythmically, his hands gripping her hips tightly, pulling her back onto him with
each powerful stroke)
```

**新增细节：**
- ✅ 解剖细节：pussy, cock, ass cheeks
- ✅ 插入细节：penetrating deep, pushes all the way in
- ✅ 流体细节：wet, pussy fully exposed and wet
- ✅ 声音细节：wet sound, moans growing louder
- ✅ 身体物理：ass cheeks rippling, body shivering
- ✅ 动作细节：thrust rhythmically, pulling her back

### 测试案例 2: Blowjob

**输入：**
```
A woman giving a blowjob
```

**旧版输出（~40 words）：**
```
(at 0 seconds: A woman giving a blowjob, moving her head up and down rhythmically,
her hand gripping the base, her eyes looking up)
```

**新版输出（~73 words）：**
```
(at 0 seconds: A woman giving a blowjob, her lips tightly wrapped around his cock,
her tongue circling the underside of his shaft, her cheeks hollowing as she takes him
deep into her throat, saliva dripping from the corner of her mouth, her head bobbing
rhythmically, her eyes looking up at him with intense focus, her hand stroking the base
of his cock, the sound of her gagging echoing with each deep suck)
```

**新增细节：**
- ✅ 解剖细节：cock, shaft, lips, tongue, throat
- ✅ 动作细节：lips wrapped around, tongue circling, takes him deep
- ✅ 流体细节：saliva dripping
- ✅ 声音细节：gagging echoing
- ✅ 身体反应：cheeks hollowing, eyes looking up

### 测试案例 3: Doggy style

**输入：**
```
Doggy style sex, hard and fast
```

**新版输出（~89 words）：**
```
(at 0 seconds: Doggy style sex, hard and fast, her on all fours, ass raised high,
his cock thrusting in and out of her pussy from behind, his hands gripping her hips
tightly, pulling her back onto him with each forceful stroke, her body rocking forward
with each thrust, her ass cheeks rippling with the impact, his cock sliding deep into
her wet pussy, wet slapping sounds echoing with every penetration, her back arching
as she moans loudly, her tits bouncing with the rhythm of his fast, pounding thrusts)
```

**新增细节：**
- ✅ 解剖细节：cock, pussy, ass, tits, ass cheeks
- ✅ 插入细节：thrusting in and out, sliding deep into, penetration
- ✅ 流体细节：wet pussy
- ✅ 声音细节：wet slapping sounds, moans loudly
- ✅ 身体物理：ass cheeks rippling, tits bouncing, body rocking
- ✅ 动作细节：pulling her back, forceful stroke, pounding thrusts

### 测试案例 4: Cumshot

**输入：**
```
He cums on her tits
```

**新版输出（~66 words）：**
```
(at 0 seconds: He cums on her tits, his cock still pulsing as thick white cum shoots
across her bare chest, coating her round, bouncing tits in heavy, glistening bursts,
her breath quickens as she gasps from the sudden warmth and wetness, her hands gripping
his shoulders, her eyes wide with pleasure, the sound of his cum splattering against
her tits echoing in the quiet space)
```

**新增细节：**
- ✅ 解剖细节：cock, tits, chest
- ✅ 流体细节：thick white cum, coating, glistening, wetness
- ✅ 动作细节：pulsing, shoots, splattering
- ✅ 声音细节：splattering echoing
- ✅ 身体反应：breath quickens, gasps, eyes wide

---

## 详细扩展统计

| 方面 | 旧版 | 新版 | 改进 |
|------|------|------|------|
| 平均字数 | 40-60 words | 70-130 words | +75-115% |
| 解剖术语 | 少量 | 丰富（cock, pussy, tits, ass, shaft, lips, tongue） | +++++ |
| 插入细节 | 基本 | 详细（深度、角度、进出、拉伸） | +++++ |
| 流体细节 | 很少 | 详细（湿润、唾液、精液、滴落） | +++++ |
| 声音细节 | 很少 | 详细（湿润声、拍打声、呻吟、窒息） | +++++ |
| 身体物理 | 基本 | 详细（弹跳、波动、摇晃、颤抖） | +++++ |
| 物理反应 | 基本 | 详细（面部表情、身体动作、发声） | +++++ |

---

## 技术实现

### 1. 扩展提示词典增强

```python
NSFW_TERMS_EXPANSION_HINTS = {
    r'\bblowjob\b': "add explicit details: how deep she takes him, her lips wrapped
    around the shaft, tongue movement along the underside, hand stroking the base,
    saliva dripping, eye contact looking up, cheeks hollowing, head bobbing rhythm,
    gagging sounds",
    # ... 25+ 详细术语扩展
}
```

### 2. 核心规则强化

```python
_SHARED_RULES = """
## Explicitness Level:
- Use anatomically explicit terms: cock, pussy, ass, tits, nipples, clit, shaft, etc.
- Describe sexual acts in detail: penetration depth, thrusting motion, body contact
- Include fluid details: wetness, cum shots, dripping, glistening
- Describe physical sensations through visible reactions
"""
```

### 3. 字数要求提升

```python
# 旧版
"Total length: 80-150 words"

# 新版
"Total length: 100-200 words (can be longer for complex scenes)"
```

---

## 使用建议

### API 调用

```python
from api.services.prompt_optimizer import PromptOptimizer

optimizer = PromptOptimizer()

result = await optimizer.optimize(
    prompt="A sexy woman is having sex with a man in the face-down ass-up position",
    trigger_words=[],
    mode="t2v",
    duration=6,
)

print(result['optimized_prompt'])
# 输出会包含详细的解剖术语、插入细节、流体描述、声音细节等
```

### 配置要求

确保使用支持 NSFW 内容的 LLM：

```bash
# .env
LLM_API_KEY=your_api_key
LLM_BASE_URL=http://your-uncensored-llm/v1
LLM_MODEL=your-uncensored-model  # 例如：Qwen3-14B-v2-Abliterated
```

---

## 总结

| 方面 | 旧版 | 新版 |
|------|------|------|
| 核心原则 | 保持原意 + 基本扩展 | 保持原意 + 详细扩展 |
| 细节程度 | 基础动作描述 | 解剖、插入、流体、声音、物理 |
| 字数 | 40-60 words | 70-130 words |
| 解剖术语 | 少量 | 丰富明确 |
| 适用场景 | 一般视频生成 | 专业成人视频生成 |
| 用户满意度 | 基本符合 | 高度符合预期 |

**关键改进：** 在保持用户原意的基础上，添加了大量大尺度细节描述，让视频生成更加具体、真实、符合用户期望。
