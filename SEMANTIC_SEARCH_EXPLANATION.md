# 语义搜索匹配逻辑详解

## 当前系统架构

### 1. Embedding模型
- **模型**: BGE-Large-ZH-v1.5 (BAAI/bge-large-zh-v1.5)
- **维度**: 1024维向量
- **优化方向**: 中文语义理解
- **相似度计算**: 内积 (IP - Inner Product)，范围 [-1, 1]，越高越相似

### 2. 匹配流程

```
用户查询 "on all fours"
    ↓
[1] 文本 → Embedding向量 (1024维)
    ↓
[2] 在向量数据库中搜索最相似的向量 (IP相似度)
    ↓
[3] 返回Top-K个最相似的LORA
    ↓
[4] 从MySQL获取完整元数据
    ↓
[5] 按相似度排序返回
```

### 3. LORA索引内容

每个LORA会被索引为多个embedding：
- **description**: LORA描述文本
- **trigger_prompt**: 触发提示词（如果有）
- **trigger_words**: 触发词列表（每个词一个embedding）
- **tags**: 标签列表（每个标签一个embedding）

例如，LORA #40 (wan_22_doggy_by_mq_lab) 有17个embeddings：
1. Description (重复4次，可能是bug)
2. Tags: "concept", "sex", "nsfw", "doggy" (各4次)

## "on all fours" 搜索案例分析

### 实际搜索结果
```
查询: "on all fours"
结果: big_breasts (similarity: 0.432)
```

### 为什么匹配到 "big_breasts"？

让我们看看BGE-Large-ZH-v1.5模型的语义理解：

| 查询 | 候选词 | 相似度 | 说明 |
|------|--------|--------|------|
| on all fours | doggy | 0.339 | 语义距离较远 |
| on all fours | face down ass up | 0.386 | 稍微相关 |
| on all fours | big breasts | 0.432 | **最高分** |
| on all fours | all fours position | 0.860 | 如果有这个词会完美匹配 |

**匹配理由**:
1. BGE-Large-ZH-v1.5是**中文优化模型**，对英文习语理解有限
2. "on all fours"在模型的embedding空间中，与"big breasts"的距离比"doggy"更近
3. 这不是bug，而是模型的语义理解特性
4. 当前启用的6个LORA中，没有一个与"on all fours"语义强相关

### 当前启用的LORA
```
ID  Name                          主要标签/描述
1   face_down_ass_up             Face Down Ass Up pose
4   reverse_suspended_congress   Reverse Suspended Congress
8   cowgirl                      POV Cowgirl
9   big_breasts                  big breasts, huge breasts
40  wan_22_doggy_by_mq_lab       concept, sex, nsfw, doggy
53  wan_22_doggy_by_mq_lab       concept, sex, nsfw, doggy
```

## 优化建议

### 方案1: 改进LORA元数据（推荐）

**问题**: 当前LORA的描述和标签不够丰富

**解决方案**:
```sql
-- 为doggy LORA添加更多相关描述
UPDATE lora_metadata
SET trigger_prompt = 'doggy style position, on all fours, from behind, rear entry position'
WHERE id IN (40, 53);

-- 添加更多标签
UPDATE lora_metadata
SET tags = JSON_ARRAY('concept', 'sex', 'nsfw', 'doggy', 'doggy style', 'all fours', 'from behind', 'rear entry')
WHERE id IN (40, 53);
```

**优点**:
- 无需更换模型
- 立即生效
- 成本低

**缺点**:
- 需要人工维护
- 每个LORA都需要优化

### 方案2: 使用多语言/领域特定模型

**替换模型选项**:

1. **multilingual-e5-large** (1024维)
   - 多语言支持更好
   - 英文理解更强
   - 但中文可能略弱

2. **text-embedding-3-large** (OpenAI, 3072维)
   - 英文理解最强
   - 需要API调用
   - 成本较高

3. **CLIP模型** (如果有图片)
   - 图文联合embedding
   - 可以直接搜索图片内容
   - 需要重新设计架构

**实施步骤**:
```python
# 在 embedding_service.py 中
from sentence_transformers import SentenceTransformer

# 替换模型
self.model = SentenceTransformer('intfloat/multilingual-e5-large', device=device)
self.dimension = 1024

# 重建索引
# POST /api/v1/admin/embeddings/rebuild-loras
```

### 方案3: 混合搜索（最佳但复杂）

**结合多种搜索方式**:
1. 语义搜索 (当前方式)
2. 关键词搜索 (BM25)
3. 标签精确匹配

**实施示例**:
```python
async def hybrid_search(query: str, top_k: int = 10):
    # 1. 语义搜索 (权重 0.6)
    semantic_results = await embedding_service.search_similar_loras(query, top_k=20)

    # 2. 关键词搜索 (权重 0.3)
    keyword_results = await keyword_search(query, top_k=20)

    # 3. 标签匹配 (权重 0.1)
    tag_results = await tag_exact_match(query, top_k=20)

    # 4. 融合分数
    final_scores = merge_scores(semantic_results, keyword_results, tag_results)

    return sorted(final_scores, key=lambda x: x['score'], reverse=True)[:top_k]
```

### 方案4: 添加同义词映射

**创建查询扩展**:
```python
QUERY_SYNONYMS = {
    "on all fours": ["doggy", "doggy style", "from behind", "rear entry"],
    "cowgirl": ["woman on top", "riding"],
    "missionary": ["face to face", "man on top"],
}

async def expand_query(query: str):
    # 如果查询有同义词，搜索所有同义词并合并结果
    synonyms = QUERY_SYNONYMS.get(query.lower(), [query])
    all_results = []

    for syn in synonyms:
        results = await embedding_service.search_similar_loras(syn, top_k=10)
        all_results.extend(results)

    # 去重并按最高分排序
    return deduplicate_and_sort(all_results)
```

## 推荐实施顺序

1. **立即实施**: 方案1 - 改进LORA元数据
   - 成本低，效果明显
   - 为每个LORA添加丰富的描述和同义词标签

2. **短期**: 方案4 - 添加同义词映射
   - 快速解决常见查询问题
   - 维护成本低

3. **中期**: 方案3 - 混合搜索
   - 显著提升搜索质量
   - 需要一定开发工作

4. **长期**: 方案2 - 评估更换模型
   - 如果用户主要使用英文查询
   - 需要全面测试和评估

## 测试和评估

### 创建测试集
```python
TEST_QUERIES = [
    ("on all fours", ["wan_22_doggy_by_mq_lab"]),  # 期望结果
    ("big breasts", ["big_breasts"]),
    ("cowgirl position", ["cowgirl"]),
    ("face down", ["face_down_ass_up"]),
]

# 计算准确率
def evaluate_search():
    correct = 0
    for query, expected_loras in TEST_QUERIES:
        results = search(query, top_k=3)
        if any(r['name'] in expected_loras for r in results):
            correct += 1

    accuracy = correct / len(TEST_QUERIES)
    print(f"搜索准确率: {accuracy * 100:.1f}%")
```

## 总结

当前"on all fours"搜索结果不理想的根本原因：
1. BGE-Large-ZH-v1.5是中文优化模型，英文习语理解有限
2. LORA元数据不够丰富，缺少同义词和详细描述
3. 纯语义搜索在某些场景下不够精确

**最快见效的方案**: 为每个LORA添加丰富的英文和中文描述、同义词标签。
