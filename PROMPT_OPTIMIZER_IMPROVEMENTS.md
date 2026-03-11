# Prompt Optimizer 优化说明

## 核心改进原则

**旧版本问题：** 改写用户原意，用委婉词替换原始术语
**新版本原则：** 保持原意 + 适当扩展

---

## 主要改动

### 1. NSFW 术语处理方式改变

**旧版 (NSFW_TERMS_DEFINITIONS):**
```python
r'\bblowjob\b': "takes him into her mouth and moves her head rhythmically, one hand gripping the base"
```
- 问题：直接替换用户的词汇，改变了原意

**新版 (NSFW_TERMS_EXPANSION_HINTS):**
```python
r'\bblowjob\b': "add details about head movement, hand positioning, eye contact"
```
- 改进：保留用户原词 "blowjob"，只添加动作细节提示

### 2. 核心规则重写

**旧版规则：**
- 强调"描述动作"，但容易导致改写原文
- 使用示例中直接替换了用户术语

**新版规则 (_SHARED_RULES):**
```
## Core Principle: PRESERVE USER'S ORIGINAL MEANING + ADD VIDEO DETAILS

## What to PRESERVE (CRITICAL):
1. Original keywords and terminology
2. Original scene structure
3. Original action sequence
4. Original intent

## What to ADD (Expansion):
1. Time markers - (at 0 seconds: ...)
2. Motion details - speed, direction, rhythm
3. Body mechanics - specific movements
4. Physical expressions - facial/body reactions
```

### 3. 模板示例改进

**旧版示例：**
```
Input: "girl takes off top, does blowjob, he cums on face"
Output: (at 0 seconds: a girl slowly taking off her top...)
        (at 3 seconds: she kneels down, takes him into her mouth...)  ← 改写了 "blowjob"
```

**新版示例：**
```
Input: "girl takes off top, does blowjob, he cums on face"
Output: (at 0 seconds: a girl takes off her top, slowly pulling it over her head...)
        (at 3 seconds: she does a blowjob, moving her head up and down...)  ← 保留了 "blowjob"
        (at 6 seconds: he cums on her face, covering her cheeks...)  ← 保留了 "cums on face"
```

---

## 实际效果对比

### 测试案例 1: Face-down ass-up position

**输入：**
```
A sexy woman is having sex with a man in the face-down ass-up position
```

**旧版可能输出：**
```
(at 0 seconds: a woman slowly lies down on her back, legs spreading wide apart,
her hands grasping his arms)
```
❌ 完全改写了姿势，从 "face-down ass-up" 变成了 "lies down on her back"

**新版输出：**
```
(at 0 seconds: A sexy woman is having sex with a man in the face-down ass-up position,
her hips raised high, her upper body pressed down against the surface, her back arching
slightly as she moves with each thrust)
```
✅ 保留了原始术语 "face-down ass-up position"，只添加了动作细节

### 测试案例 2: Multiple actions

**输入：**
```
girl takes off top, does blowjob, he cums on face
```

**新版输出：**
```
(at 0 seconds: a girl takes off her top, slowly pulling it over her head, her arms lifting,
revealing her breasts)
(at 3 seconds: she does a blowjob, moving her head up and down rhythmically, her hand
gripping the base, her eyes looking up)
(at 6 seconds: he cums on her face, covering her cheeks and lips, her eyes widening,
her mouth opening)
```
✅ 保留了所有关键词：takes off top, blowjob, cums on face
✅ 添加了时间轴和动作细节
✅ 没有改变用户意图

---

## 技术实现细节

### 1. 术语检测改进

```python
# 旧版：返回替换定义
def _detect_nsfw_terms(self, prompt: str) -> dict:
    detected[term] = definition  # "takes him into her mouth..."

# 新版：返回扩展提示
def _detect_nsfw_terms(self, prompt: str) -> dict:
    detected[term] = hint  # "add details about head movement..."
```

### 2. LLM Prompt 改进

```python
# 旧版：强制替换
user_msg += "When you see these terms, use EXACTLY these descriptions:\n"
user_msg += f"- '{term}' → {definition}\n"

# 新版：引导扩展
user_msg += "Keep the terms EXACTLY as written, but expand with these details:\n"
user_msg += f"- '{term}' → keep this term, {hint}\n"
```

### 3. LoRA 示例处理改进

```python
# 旧版：强制遵循格式
user_msg += "**IMPORTANT**: Follow the same format and style as shown in the examples above.\n"

# 新版：参考风格但保留用户意图
user_msg += "**IMPORTANT**: Learn the format and style from examples, but keep the user's original keywords and actions.\n"
```

---

## 使用建议

### API 调用示例

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
# 输出会保留 "face-down ass-up position" 并添加动作细节
```

### 配置要求

确保 `.env` 文件中配置了 LLM：

```bash
LLM_API_KEY=your_api_key
LLM_BASE_URL=http://your-llm-endpoint/v1
LLM_MODEL=your-model-name
```

---

## 总结

| 方面 | 旧版 | 新版 |
|------|------|------|
| 核心原则 | 改写描述 | 保持原意 + 扩展 |
| 术语处理 | 替换为委婉描述 | 保留原词 + 添加细节 |
| 用户意图 | 可能偏离 | 严格保持 |
| 输出质量 | 可能不符合预期 | 符合用户期望 |
| 适用场景 | 需要审查的场景 | 专业视频生成 |

**关键改进：** 从"改写"变为"扩展"，确保生成的视频与用户想要的效果一致。
