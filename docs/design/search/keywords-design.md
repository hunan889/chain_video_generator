# 搜索关键词系统设计方案（LORA + 图片）

## 问题分析

### 当前问题

1. **LORA搜索字段混乱**
   - 使用 description + trigger_words + tags（前3个）
   - 字段用途不明确
   - 管理员无法精确控制搜索结果

2. **图片搜索字段单一**
   - 只使用 prompt 字段建索引
   - 用户可能用不同的词描述同一张图
   - 无法精确控制搜索结果

3. **字段职责不清**
   - `trigger_words`: 应该用于生成提示词
   - `tags`: 用于分类和展示
   - `description`: 用于说明
   - 缺少专门的搜索字段

4. **同义词扩充问题**
   - 扩充了640+同义词到tags
   - 但全部索引会导致搜索结果混乱
   - 无法精确控制哪些词应该被搜索到

---

## 解决方案：统一的搜索关键词架构

### 核心思路

**职责分离**：
- `search_keywords`: 专门用于搜索匹配（管理员可控）
- `trigger_prompt` / `prompt`: 专门用于生成提示词（放入workflow）

**统一架构**：
- LORA 和图片使用相同的搜索字段设计
- 保持一致的管理界面和用户体验

### 字段设计

#### 1. search_keywords（新增）

**用途**: 搜索匹配

**类型**: TEXT

**内容格式**: 支持关键词列表或自然语言描述（或混合）

**重要**: 使用语义向量搜索（BGE-Large-ZH-v1.5），支持自然语言理解

**示例**:
```
# 方式1: 关键词列表
doggy LORA:
search_keywords = "doggy, doggy style, from behind, rear entry, on all fours, back view, doggystyle"

# 方式2: 自然语言描述
doggy LORA:
search_keywords = "A woman having sex from behind in doggy style position, on all fours"

# 方式3: 混合（推荐）
doggy LORA:
search_keywords = "doggy style, from behind, A woman on all fours being penetrated from behind"

# 图片示例
beach image:
search_keywords = "beach, ocean, sunset, tropical beach scene with palm trees and golden hour lighting"
```

**特点**:
- 支持关键词和自然语言混合
- 管理员可编辑
- 语义向量自动理解含义
- 灵活且强大

#### 2. trigger_prompt / prompt（已存在）

**LORA**: `trigger_prompt`
**图片**: `prompt`

**用途**: 生成提示词

**类型**: TEXT

**内容**: 完整的提示词描述

**示例**:
```
doggy LORA:
trigger_prompt = "A woman is having sex in the doggy style position, on all fours, from behind"

cowgirl LORA:
trigger_prompt = "A woman is riding on top, cowgirl position, bouncing up and down"

beach image:
prompt = "A beautiful tropical beach at sunset, golden hour lighting, palm trees, turquoise water"
```

**特点**:
- 完整的自然语言描述
- 用于放入ComfyUI workflow或参考
- 不用于搜索

---

## 数据库变更

### 1. 添加字段

```sql
-- LORA表
ALTER TABLE lora_metadata
ADD COLUMN search_keywords TEXT COMMENT '搜索关键词（支持关键词列表或自然语言描述）';

-- 图片表
ALTER TABLE resources
ADD COLUMN search_keywords TEXT COMMENT '搜索关键词（支持关键词列表或自然语言描述）';
```

### 2. 字段对比

#### LORA (lora_metadata)

| 字段 | 用途 | 示例 | 可编辑 |
|------|------|------|--------|
| `name` | LORA名称 | wan_22_doggy_by_mq_lab | ❌ |
| `description` | 说明文字 | Wan 2.2 Doggy LoRA by MQ Lab... | ✅ |
| `search_keywords` | **搜索匹配** | doggy, from behind, rear entry | ✅ |
| `trigger_prompt` | **生成提示词** | A woman is having sex in doggy style... | ✅ |
| `tags` | 分类标签 | ["position", "nsfw", "concept"] | ✅ |
| `category` | 类别 | position | ✅ |

#### 图片 (resources)

| 字段 | 用途 | 示例 | 可编辑 |
|------|------|------|--------|
| `filename` | 文件名 | beach_sunset_001.jpg | ❌ |
| `search_keywords` | **搜索匹配** | beach, sunset, tropical, golden hour | ✅ |
| `prompt` | **原始提示词** | A beautiful tropical beach at sunset... | ✅ |
| `tags` | 分类标签 | ["landscape", "nature", "beach"] | ✅ |

---

## 搜索逻辑

### LORA搜索

#### 修改前（当前）

```python
# scripts/build_lora_index_only.py
example_prompts = []
example_prompts.append(description)
example_prompts.extend(trigger_words)
example_prompts.extend(tags[:3])  # 混乱
```

#### 修改后（新方案）

```python
# scripts/build_lora_index_only.py
example_prompts = []

# 只使用 search_keywords
if lora['search_keywords']:
    # 直接使用整个字符串（支持自然语言）
    example_prompts.append(lora['search_keywords'])

# 如果没有 search_keywords，fallback 到 name
if not example_prompts:
    example_prompts.append(lora['name'])
```

### 图片搜索

#### 修改前（当前）

```python
# scripts/build_initial_index.py
example_prompts = []
if resource['prompt']:
    example_prompts.append(resource['prompt'])
```

#### 修改后（新方案）

```python
# scripts/build_initial_index.py
example_prompts = []

# 优先使用 search_keywords
if resource['search_keywords']:
    example_prompts.append(resource['search_keywords'])
# fallback 到 prompt
elif resource['prompt']:
    example_prompts.append(resource['prompt'])
# 最后 fallback 到文件名
else:
    example_prompts.append(resource['filename'])
```

**优点**:
- 搜索结果可控
- 字段职责清晰
- 管理员可精确控制
- 支持自然语言和关键词混合

---

## 自动生成 search_keywords

### LORA: 基于同义词库

```python
def generate_lora_search_keywords(lora_name: str, tags: list, description: str = "") -> str:
    """自动生成LORA搜索关键词"""
    keywords = set()

    # 1. 从LORA名称中提取关键词
    name_words = lora_name.lower().replace('_', ' ').split()
    for word in name_words:
        if word in SYNONYM_TO_KEY:
            key = SYNONYM_TO_KEY[word]
            # 添加KEY本身
            keywords.add(key)
            # 添加最常用的5-7个同义词
            if key in POSITION_SYNONYMS:
                keywords.update(POSITION_SYNONYMS[key][:7])
            elif key in ACTION_SYNONYMS:
                keywords.update(ACTION_SYNONYMS[key][:7])
            elif key in BODY_SYNONYMS:
                keywords.update(BODY_SYNONYMS[key][:5])
            elif key in SCENARIO_SYNONYMS:
                keywords.update(SCENARIO_SYNONYMS[key][:5])

    # 2. 从tags中提取关键词
    for tag in tags[:5]:
        tag_lower = tag.lower()
        if tag_lower in SYNONYM_TO_KEY:
            key = SYNONYM_TO_KEY[tag_lower]
            keywords.add(key)

    # 3. 限制数量并格式化
    keywords_list = list(keywords)[:15]

    # 4. 如果有描述，可以添加自然语言部分（可选）
    if description and len(keywords_list) < 10:
        # 提取描述中的关键句子
        desc_short = description.split('.')[0] if '.' in description else description
        if len(desc_short) < 100:
            keywords_list.append(desc_short)

    return ', '.join(keywords_list)
```

**示例输出**:
```
doggy LORA:
"doggy, doggy style, from behind, rear entry, on all fours, back view, rear view, prone bone"

cowgirl LORA:
"cowgirl, woman on top, riding, girl on top, reverse cowgirl, bouncing, grinding"
```

### 图片: 基于prompt提取

```python
def generate_image_search_keywords(filename: str, prompt: str = "", tags: list = []) -> str:
    """自动生成图片搜索关键词"""
    keywords = []

    # 1. 从tags中提取
    if tags:
        keywords.extend(tags[:5])

    # 2. 从prompt中提取关键词（简单版本）
    if prompt:
        # 提取名词和形容词（简化版）
        words = prompt.lower().replace(',', ' ').split()
        # 过滤常见词
        stop_words = {'a', 'an', 'the', 'is', 'are', 'with', 'at', 'in', 'on'}
        keywords.extend([w for w in words if w not in stop_words and len(w) > 3][:10])

    # 3. 从文件名中提取
    if not keywords:
        name_parts = filename.replace('_', ' ').replace('-', ' ').split()
        keywords.extend([p for p in name_parts if len(p) > 3][:5])

    # 4. 去重并限制数量
    keywords = list(dict.fromkeys(keywords))[:15]

    # 5. 如果prompt较短，直接使用
    if prompt and len(prompt) < 150:
        return prompt

    return ', '.join(keywords)
```

**示例输出**:
```
beach image:
"beach, sunset, tropical, palm trees, golden hour, ocean, turquoise water"

或自然语言:
"A beautiful tropical beach at sunset with palm trees and golden hour lighting"
```

---

## 前端管理界面

### LORA管理页面新增字段

```html
<div class="edit-field">
  <label>搜索关键词 (Search Keywords)</label>
  <textarea
    placeholder="支持关键词列表或自然语言描述，例如：&#10;doggy, from behind, rear entry&#10;或：A woman having sex in doggy style position"
    rows="3">{{search_keywords}}</textarea>
  <small>用于搜索匹配，支持关键词列表或自然语言描述</small>
  <button class="btn-sm btn-secondary" onclick="generateSearchKeywords(this)">
    🤖 自动生成
  </button>
</div>

<div class="edit-field">
  <label>触发提示词 (Trigger Prompt)</label>
  <textarea placeholder="A woman is having sex in doggy style position..." rows="3">
    {{trigger_prompt}}
  </textarea>
  <small>用于生成视频，完整的自然语言描述</small>
</div>
```

### 图片管理页面新增字段

```html
<div class="edit-field">
  <label>搜索关键词 (Search Keywords)</label>
  <textarea
    placeholder="支持关键词列表或自然语言描述，例如：&#10;beach, sunset, tropical&#10;或：A beautiful tropical beach at sunset"
    rows="3">{{search_keywords}}</textarea>
  <small>用于搜索匹配，支持关键词列表或自然语言描述</small>
  <button class="btn-sm btn-secondary" onclick="generateImageSearchKeywords(this)">
    🤖 自动生成
  </button>
</div>

<div class="edit-field">
  <label>原始提示词 (Prompt)</label>
  <textarea placeholder="原始生成提示词..." rows="3">
    {{prompt}}
  </textarea>
  <small>用于参考和复用</small>
</div>
```

---

## 实施步骤

### 第1步: 数据库变更

```bash
# 连接数据库
mysql -h use-cdb-b9nvte6o.sql.tencentcdb.com -P 20603 -u user_soga -p tudou_soga
```

```sql
-- 添加字段
ALTER TABLE lora_metadata
ADD COLUMN search_keywords TEXT COMMENT '搜索关键词（支持关键词列表或自然语言描述）';

ALTER TABLE resources
ADD COLUMN search_keywords TEXT COMMENT '搜索关键词（支持关键词列表或自然语言描述）';

-- 查看结果
SELECT id, name, search_keywords FROM lora_metadata LIMIT 5;
SELECT id, filename, search_keywords FROM resources LIMIT 5;
```

### 第2步: 创建自动生成脚本

创建 `scripts/generate_search_keywords.py`:
- 为所有LORA生成 search_keywords
- 为所有图片生成 search_keywords
- 支持 --dry-run 预览
- 支持 --apply 实际应用

### 第3步: 运行自动生成

```bash
# 预览效果（不修改数据库）
python scripts/generate_search_keywords.py --dry-run

# 实际应用到所有资源
python scripts/generate_search_keywords.py --apply

# 只处理LORA
python scripts/generate_search_keywords.py --apply --lora-only

# 只处理图片
python scripts/generate_search_keywords.py --apply --image-only
```

### 第4步: 修改索引构建脚本

修改 `scripts/build_lora_index_only.py`:
```python
# 只使用 search_keywords
if lora['search_keywords']:
    example_prompts.append(lora['search_keywords'])
elif lora['name']:
    example_prompts.append(lora['name'])
```

修改 `scripts/build_initial_index.py`:
```python
# 优先使用 search_keywords
if resource['search_keywords']:
    example_prompts.append(resource['search_keywords'])
elif resource['prompt']:
    example_prompts.append(resource['prompt'])
else:
    example_prompts.append(resource['filename'])
```

### 第5步: 重建索引

```bash
# 重建LORA索引
python scripts/build_lora_index_only.py

# 重建完整索引（包括图片）
python scripts/build_initial_index.py
```

### 第6步: 前端界面更新

修改 `api/static/lora_manager.html`:
- 添加 search_keywords 输入框
- 添加"自动生成"按钮
- 更新保存逻辑

（图片管理界面类似修改）

### 第7步: API更新

修改 `api/routes/lora_admin.py`:
- 支持读取/更新 search_keywords
- 添加自动生成接口 `/api/lora/generate-keywords/{lora_id}`

（图片API类似修改）

---

## 优势对比

### 当前方案（混乱）

❌ LORA使用 description + trigger_words + tags[:3]
❌ 图片只使用 prompt
❌ 字段职责不清
❌ 管理员无法控制
❌ 搜索结果不可预测

### 新方案（清晰）

✅ 统一的 search_keywords 字段
✅ 职责分离（搜索 vs 提示词）
✅ 管理员完全可控
✅ 搜索结果可预测
✅ 支持自动生成
✅ 支持手动编辑
✅ 支持关键词和自然语言混合
✅ LORA和图片使用相同架构

---

## 示例对比

### doggy LORA

**当前**:
```
索引内容:
- description: "Wan 2.2 Spoon LoRA by MQ Lab..."
- trigger_words: []
- tags[:3]: ["back entry", "rear entry", "doggy"]
```

**新方案**:
```
search_keywords: "doggy, doggy style, from behind, rear entry, on all fours, back view, rear view"
trigger_prompt: "A woman is having sex in the doggy style position, on all fours, from behind"

索引内容: 只使用 search_keywords
```

### beach 图片

**当前**:
```
索引内容:
- prompt: "A beautiful tropical beach at sunset, golden hour lighting, palm trees, turquoise water, 8k, highly detailed"
```

**新方案**:
```
search_keywords: "beach, sunset, tropical, palm trees, golden hour, ocean, turquoise water"
prompt: "A beautiful tropical beach at sunset, golden hour lighting, palm trees, turquoise water, 8k, highly detailed"

索引内容: 只使用 search_keywords
```

---

## 后续优化

### 1. 搜索关键词推荐

根据搜索日志，推荐热门关键词：
```
用户经常搜索 "rear view" 找到 doggy LORA
→ 系统建议: 将 "rear view" 添加到 search_keywords
```

### 2. 多语言支持

```sql
ALTER TABLE lora_metadata
ADD COLUMN search_keywords_zh TEXT COMMENT '中文搜索关键词';

ALTER TABLE resources
ADD COLUMN search_keywords_zh TEXT COMMENT '中文搜索关键词';

-- 示例
search_keywords = "doggy, from behind, rear entry"
search_keywords_zh = "后入, 狗爬式, 后背位"
```

### 3. A/B测试

对比新旧搜索方案的效果：
- 搜索召回率
- 用户满意度
- 搜索成功率

---

## 总结

### 核心改进

1. **统一架构**: LORA和图片使用相同的 search_keywords 设计
2. **职责分离**: search_keywords（搜索） vs trigger_prompt/prompt（提示词）
3. **管理员可控**: 可编辑、可预测
4. **自动化**: 支持批量生成
5. **灵活性**: 支持关键词列表或自然语言描述

### 技术优势

- 语义向量搜索（BGE-Large-ZH-v1.5）支持自然语言理解
- 不依赖精确关键词匹配
- 用户搜索体验更好

### 实施计划

1. ✅ 设计方案完成
2. ⏳ 数据库变更
3. ⏳ 创建自动生成脚本
4. ⏳ 修改索引构建脚本
5. ⏳ 重建向量索引
6. ⏳ 更新前端界面
7. ⏳ 更新API接口
8. ⏳ 测试验证

---

## 预期效果

### 搜索召回率提升

**LORA搜索**:
- 用户搜索 "from behind" → 找到所有 doggy 相关LORA
- 用户搜索 "woman on top" → 找到所有 cowgirl 相关LORA
- 用户搜索 "rear view" → 找到所有后入视角LORA

**图片搜索**:
- 用户搜索 "beach sunset" → 找到所有海滩日落图片
- 用户搜索 "tropical paradise" → 找到热带风景图片
- 用户搜索 "golden hour" → 找到黄金时段拍摄的图片

### 预计提升指标

- **搜索召回率**: +50-80%
- **用户满意度**: +30-50%
- **搜索成功率**: +40-60%
- **管理效率**: +70%（可控的搜索关键词）
