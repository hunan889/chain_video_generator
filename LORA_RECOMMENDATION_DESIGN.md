# LORA推荐系统设计方案

## 一、现状分析

### 1.1 已有功能
- **LORA基础管理**: `config/loras.yaml` 存储LORA元数据（名称、描述、tags、trigger_words、example_prompts）
- **LLM推荐**: `api/services/lora_selector.py` 通过LLM根据prompt推荐LORA
- **资源标注系统**: `api/routes/resources.py` 实现了图片/视频的标签管理
  - 数据库表：`resources`（资源）、`tags`（标签）、`resource_tags`（关联）、`favorites`（收藏）
  - 标签分类：action, body_parts, clothing, expression, modifier, position, scene
  - 支持标签搜索、收藏功能
- **数据规模**:
  - 图片资源：260,662条
  - 视频资源：17,519条
  - LORA数量：约100+个（来自loras.yaml）

### 1.2 存在的问题
1. **LORA元数据未入库**: loras.yaml中的LORA信息未同步到数据库（`lora_metadata`表为空）
2. **缺少LORA分类管理**: LORA没有按类型（action, body, scene等）分类
3. **缺少LORA收藏功能**: 无法标记和管理常用LORA
4. **缺少LORA-资源关联**: 无法记录哪些LORA适合哪些图片/视频
5. **缺少语义搜索**: 仅基于LLM推荐，无法通过prompt相似度搜索历史资源和LORA

---

## 二、设计目标

用户输入prompt后，系统应返回：
1. **相似图片**：与prompt描述相似的第一帧画面（用于I2V）
2. **图片LORA推荐**：适合生成该图片的LORA + 触发词 + 完整prompt
3. **视频LORA推荐**：适合生成该视频的LORA + 触发词 + 完整prompt

---

## 三、技术方案

### 3.1 数据库设计

#### 3.1.1 LORA元数据表（已存在，需填充数据）
```sql
-- lora_metadata 表结构
CREATE TABLE lora_metadata (
  id INT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(200) NOT NULL,              -- LORA名称
  file VARCHAR(200) NOT NULL UNIQUE,       -- 文件名
  category VARCHAR(50),                     -- 分类：action/body/scene/style等
  modifiers JSON,                           -- 修饰符（如strength范围）
  trigger_words JSON,                       -- 触发词列表
  civitai_id INT UNIQUE,
  civitai_version_id INT,
  preview_url TEXT,
  description TEXT,
  tags JSON,                                -- 标签数组
  mode ENUM('I2V','T2V','both') DEFAULT 'both',  -- 适用模式
  noise_stage ENUM('high','low','single') DEFAULT 'single',
  quality_score TINYINT,                    -- 质量评分（1-10）
  download_count INT DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_category (category),
  INDEX idx_mode (mode)
);
```

#### 3.1.2 LORA收藏表（新增）
```sql
CREATE TABLE lora_favorites (
  id INT PRIMARY KEY AUTO_INCREMENT,
  lora_id INT NOT NULL,
  note TEXT,                                -- 收藏备注
  custom_strength FLOAT,                    -- 自定义强度
  custom_trigger_words JSON,                -- 自定义触发词
  usage_count INT DEFAULT 0,                -- 使用次数
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_lora (lora_id),
  FOREIGN KEY (lora_id) REFERENCES lora_metadata(id) ON DELETE CASCADE
);
```

#### 3.1.3 资源-LORA推荐关联表（已存在）
```sql
-- resource_lora_recommendations 表结构
CREATE TABLE resource_lora_recommendations (
  resource_id BIGINT NOT NULL,
  lora_id INT NOT NULL,
  score FLOAT DEFAULT 1.0,                  -- 推荐分数
  source ENUM('auto','manual') DEFAULT 'auto',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (resource_id, lora_id),
  INDEX idx_score (score),
  FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE,
  FOREIGN KEY (lora_id) REFERENCES lora_metadata(id) ON DELETE CASCADE
);
```

#### 3.1.4 Prompt嵌入向量表（新增，用于语义搜索）
```sql
CREATE TABLE prompt_embeddings (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  resource_id BIGINT,                       -- 关联资源（可选）
  lora_id INT,                              -- 关联LORA（可选）
  prompt TEXT NOT NULL,                     -- 原始prompt
  embedding BLOB NOT NULL,                  -- 向量（存储为二进制）
  embedding_model VARCHAR(50) DEFAULT 'text-embedding-3-small',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_resource (resource_id),
  INDEX idx_lora (lora_id),
  FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE,
  FOREIGN KEY (lora_id) REFERENCES lora_metadata(id) ON DELETE CASCADE
);
```

### 3.2 功能模块设计

#### 3.2.1 LORA管理模块（扩展现有功能）

**API端点**：
```python
# 1. LORA列表（支持分类筛选、收藏筛选）
GET /api/v1/loras?category=action&favorited=true&mode=I2V

# 2. LORA详情
GET /api/v1/loras/{lora_id}

# 3. 添加/取消收藏
POST /api/v1/loras/{lora_id}/favorite
DELETE /api/v1/loras/{lora_id}/favorite

# 4. 更新LORA元数据（分类、自定义触发词等）
PATCH /api/v1/loras/{lora_id}

# 5. 批量导入LORA到数据库（从loras.yaml同步）
POST /api/v1/loras/sync
```

**实现要点**：
- 扩展 `api/routes/loras.py`，添加分类、收藏功能
- 创建 `api/services/lora_manager.py` 处理LORA元数据同步

#### 3.2.2 语义搜索模块（新增）

**核心服务**：`api/services/semantic_search.py`

```python
class SemanticSearchService:
    def __init__(self):
        self.embedding_model = "text-embedding-3-small"  # OpenAI或本地模型

    async def embed_prompt(self, prompt: str) -> np.ndarray:
        """生成prompt的向量表示"""
        # 调用OpenAI API或本地embedding模型
        pass

    async def search_similar_resources(
        self,
        prompt: str,
        resource_type: str = "image",
        top_k: int = 10
    ) -> List[Resource]:
        """搜索与prompt相似的资源"""
        # 1. 生成query向量
        # 2. 从prompt_embeddings表中检索相似向量（余弦相似度）
        # 3. 返回关联的资源
        pass

    async def search_similar_loras(
        self,
        prompt: str,
        mode: str = "I2V",
        top_k: int = 5
    ) -> List[LoraRecommendation]:
        """搜索与prompt相似的LORA"""
        # 1. 生成query向量
        # 2. 从prompt_embeddings表中检索LORA相关的向量
        # 3. 结合LORA的tags、trigger_words进行二次排序
        pass

    async def index_resource(self, resource_id: int, prompt: str):
        """为资源建立索引"""
        embedding = await self.embed_prompt(prompt)
        # 存储到prompt_embeddings表
        pass

    async def index_lora(self, lora_id: int):
        """为LORA建立索引（基于example_prompts和trigger_words）"""
        # 从lora_metadata获取example_prompts
        # 为每个example_prompt生成embedding
        pass
```

**API端点**：
```python
# 1. 语义搜索资源
POST /api/v1/search/resources
{
  "prompt": "a woman in doggy style position",
  "resource_type": "image",
  "top_k": 10
}

# 2. 语义搜索LORA
POST /api/v1/search/loras
{
  "prompt": "a woman having orgasm",
  "mode": "I2V",
  "top_k": 5
}

# 3. 综合推荐（资源+LORA）
POST /api/v1/recommend
{
  "prompt": "a sexy woman in cowgirl position",
  "include_images": true,
  "include_video_loras": true,
  "include_image_loras": true
}
```

#### 3.2.3 LORA推荐模块（增强现有功能）

**增强策略**：
1. **混合推荐**：LLM推荐 + 语义搜索 + 标签匹配
2. **上下文感知**：根据用户历史收藏和使用频率调整推荐
3. **关联推荐**：基于资源-LORA关联表推荐

```python
class EnhancedLoraSelector:
    async def recommend(
        self,
        prompt: str,
        mode: str = "I2V",
        use_llm: bool = True,
        use_semantic: bool = True,
        use_favorites: bool = True
    ) -> List[LoraRecommendation]:
        """综合推荐LORA"""
        results = []

        # 1. LLM推荐（现有功能）
        if use_llm:
            llm_results = await self.llm_selector.select(prompt)
            results.extend(llm_results)

        # 2. 语义搜索推荐
        if use_semantic:
            semantic_results = await self.semantic_search.search_similar_loras(prompt, mode)
            results.extend(semantic_results)

        # 3. 收藏LORA优先
        if use_favorites:
            favorite_loras = await self.get_favorite_loras()
            # 对收藏的LORA进行标签匹配
            matched = self.match_by_tags(prompt, favorite_loras)
            results.extend(matched)

        # 4. 去重、排序、返回top-N
        return self.deduplicate_and_rank(results)
```

#### 3.2.4 资源标注增强（扩展现有功能）

**新增功能**：
1. **LORA关联标注**：在标注资源时，可以关联推荐的LORA
2. **批量标注**：支持批量为资源添加LORA推荐
3. **自动标注**：生成视频时自动记录使用的LORA

**API端点**：
```python
# 1. 为资源添加LORA推荐
POST /api/v1/resources/{resource_id}/loras
{
  "lora_id": 123,
  "score": 0.9,
  "source": "manual"
}

# 2. 获取资源的LORA推荐
GET /api/v1/resources/{resource_id}/loras

# 3. 批量标注
POST /api/v1/resources/batch-annotate
{
  "resource_ids": [1, 2, 3],
  "lora_ids": [10, 20]
}
```

### 3.3 前端UI设计

#### 3.3.1 LORA管理界面（新增Tab）

```
[T2V] [I2V] [Chain] [LORA管理] [资源库]

LORA管理页面：
┌─────────────────────────────────────────┐
│ 分类筛选: [全部▼] [Action] [Body] ...  │
│ 模式筛选: [全部▼] [I2V] [T2V]          │
│ 显示收藏: [✓]                           │
├─────────────────────────────────────────┤
│ ┌──────┐ ┌──────┐ ┌──────┐             │
│ │LORA1 │ │LORA2 │ │LORA3 │             │
│ │[★]   │ │[☆]   │ │[★]   │             │
│ │Action│ │Body  │ │Scene │             │
│ │0.8   │ │0.7   │ │0.6   │             │
│ └──────┘ └──────┘ └──────┘             │
└─────────────────────────────────────────┘
```

#### 3.3.2 智能推荐界面（增强现有生成页面）

在T2V/I2V页面添加"智能推荐"按钮：

```
Prompt: [输入框]  [智能推荐🔍]

点击后弹出推荐面板：
┌─────────────────────────────────────────┐
│ 推荐结果                                 │
├─────────────────────────────────────────┤
│ 📷 相似图片 (3)                          │
│ ┌────┐ ┌────┐ ┌────┐                   │
│ │img1│ │img2│ │img3│ [使用]            │
│ └────┘ └────┘ └────┘                   │
├─────────────────────────────────────────┤
│ 🎨 推荐图片LORA (2)                      │
│ ☑ instagirl_v2 (0.7)                    │
│   触发词: Instagirl, l3n0v0             │
│ ☑ big_breasts (0.6)                     │
├─────────────────────────────────────────┤
│ 🎬 推荐视频LORA (3)                      │
│ ☑ cowgirl (0.8)                         │
│   触发词: cowgirl position              │
│ ☐ orgasm (0.7)                          │
│ ☐ breast_play (0.6)                     │
├─────────────────────────────────────────┤
│ [应用选中项] [取消]                      │
└─────────────────────────────────────────┘
```

---

## 四、实施计划

### Phase 1: 数据准备（1-2天）
1. ✅ 创建新数据库表（`lora_favorites`, `prompt_embeddings`）
2. ✅ 编写LORA同步脚本，将`loras.yaml`数据导入`lora_metadata`表
3. ✅ 为LORA添加分类标签（手动或半自动）
4. ✅ 为现有资源生成prompt embeddings（批量处理）

### Phase 2: 后端开发（3-4天）
1. ✅ 实现LORA管理API（分类、收藏、更新）
2. ✅ 实现语义搜索服务（embedding生成、相似度检索）
3. ✅ 增强LORA推荐逻辑（混合推荐）
4. ✅ 实现资源-LORA关联API

### Phase 3: 前端开发（2-3天）
1. ✅ 开发LORA管理界面（分类浏览、收藏管理）
2. ✅ 集成智能推荐面板到T2V/I2V页面
3. ✅ 优化资源标注界面（支持LORA关联）

### Phase 4: 测试与优化（1-2天）
1. ✅ 功能测试
2. ✅ 性能优化（向量检索速度、数据库查询）
3. ✅ 用户体验优化

---

## 五、技术选型

### 5.1 Embedding模型
**方案A（推荐）**: OpenAI `text-embedding-3-small`
- 优点：质量高、API简单、维护成本低
- 缺点：需要API调用成本
- 成本：$0.02/1M tokens（约500万个prompt仅需$10）

**方案B**: 本地模型（如`sentence-transformers`）
- 优点：无API成本、数据隐私
- 缺点：需要GPU资源、维护成本高
- 推荐模型：`all-MiniLM-L6-v2`（轻量）或`paraphrase-multilingual-mpnet-base-v2`（多语言）

### 5.2 向量检索
**方案A（推荐）**: MySQL + 余弦相似度计算
- 适用于中小规模（<100万条）
- 实现简单，无需额外组件
- 查询示例：
```sql
SELECT resource_id,
       (embedding · query_embedding) / (||embedding|| * ||query_embedding||) AS similarity
FROM prompt_embeddings
ORDER BY similarity DESC
LIMIT 10;
```

**方案B**: 向量数据库（Milvus/Qdrant/Weaviate）
- 适用于大规模（>100万条）
- 需要额外部署和维护
- 仅在性能瓶颈时考虑

### 5.3 LORA分类体系
参考现有标签分类，建议LORA分类：
- **action**: 动作类（cowgirl, blowjob, orgasm等）
- **body**: 身体特征（big_breasts, body_type等）
- **scene**: 场景类（lighting, environment等）
- **style**: 风格类（instagirl, realistic等）
- **modifier**: 修饰类（quality enhancers等）
- **position**: 姿势类（missionary, doggy等）

---

## 六、关键问题与解决方案

### Q1: 如何处理LORA的HIGH/LOW noise变体？
**方案**:
- 在`lora_metadata`表中用`noise_stage`字段区分
- 推荐时根据用户选择的模型（A14B/5B）自动匹配
- UI上合并显示（如"cowgirl (HIGH/LOW)"）

### Q2: 如何提高推荐准确性？
**方案**:
1. **多策略融合**: LLM + 语义搜索 + 标签匹配 + 协同过滤
2. **用户反馈**: 记录用户实际使用的LORA，优化推荐模型
3. **A/B测试**: 对比不同推荐策略的效果

### Q3: 如何处理prompt的多语言问题？
**方案**:
- 使用多语言embedding模型（如OpenAI的模型天然支持）
- 或在embedding前统一翻译为英文

### Q4: 向量检索性能如何优化？
**方案**:
1. **索引优化**: 为`prompt_embeddings`表添加合适的索引
2. **缓存**: 对热门prompt的推荐结果进行缓存
3. **预计算**: 定期预计算LORA之间的相似度矩阵
4. **分批检索**: 先用标签/分类粗筛，再用向量精排

---

## 七、预期效果

1. **用户体验提升**:
   - 输入prompt后一键获取相似图片、推荐LORA和完整prompt
   - 减少手动选择LORA的时间，提高生成效率

2. **内容质量提升**:
   - 基于历史优质资源推荐，提高生成成功率
   - 自动组合合适的LORA，避免冲突

3. **数据资产积累**:
   - 建立资源-LORA知识库，持续优化推荐
   - 为后续AI训练提供高质量标注数据

---

## 八、后续扩展方向

1. **智能Prompt优化**: 基于推荐的LORA自动补充trigger words
2. **风格迁移**: 根据参考图片推荐LORA组合
3. **个性化推荐**: 基于用户历史偏好定制推荐
4. **LORA组合优化**: 自动检测LORA冲突，推荐最佳组合
5. **社区分享**: 用户可以分享自己的LORA配置和prompt模板
