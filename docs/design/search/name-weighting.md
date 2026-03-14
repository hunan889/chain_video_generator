# LORA搜索名称加权方案

## 问题

当多个LORA包含相同标签时，名称更匹配的LORA应该排在前面。

**示例**：搜索"from behind"时，"from_behind_style"应该比"wan_22_doggy_by_mq_lab"排名更高。

---

## 解决方案

### 核心算法

```python
最终分数 = 标签相似度 × 80% + 名称相似度 × 20%
```

**默认**: `name_weight = 0.2`（推荐）

### 名称相似度计算

三层策略（按优先级）：

1. **子串匹配** (0.8-1.0)：查询词在名称中 → `"from_behind" in "from_behind_style"` = 0.93
2. **词重叠** (0.5-0.8)：共同单词 → Jaccard相似度
3. **语义相似度** (0-0.5)：embedding相似度（fallback）

---

## 使用方法

### API调用

```bash
# 默认权重 (0.2)
curl -X POST http://localhost:8000/api/v1/search/loras \
  -H "X-API-Key: wan22-default-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"query": "from behind", "top_k": 10}'

# 自定义权重
curl -X POST http://localhost:8000/api/v1/search/loras \
  -H "X-API-Key: wan22-default-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"query": "from behind", "top_k": 10, "name_weight": 0.3}'

# 禁用名称加权
curl -X POST http://localhost:8000/api/v1/search/loras \
  -H "X-API-Key: wan22-default-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"query": "from behind", "top_k": 10, "name_weight": 0.0}'
```

### 权重选择

| name_weight | 说明 | 推荐场景 |
|-------------|------|---------|
| 0.0 | 完全忽略名称 | 测试/对比 |
| **0.2** | **默认值** | **大多数场景** ✓ |
| 0.3 | 名称影响较大 | 名称很重要 |
| 0.5 | 名称和标签同等重要 | 特殊需求 |

---

## 效果示例

### 查询: "cowgirl position"

**使用名称加权 (0.2)**:
```json
[
  {"lora_id": 8, "name": "cowgirl", "similarity": 0.685},  // 名称匹配 ✓
  {"lora_id": 40, "name": "wan_22_doggy_by_mq_lab", "similarity": 0.469}
]
```

**不使用名称加权 (0.0)**:
```json
[
  {"lora_id": 8, "name": "cowgirl", "similarity": 0.694},
  {"lora_id": 40, "name": "wan_22_doggy_by_mq_lab", "similarity": 0.549}
]
```

---

## 实施细节

### 新增文件
- `api/services/embedding_service_v2.py` - V2搜索服务

### 修改文件
- `api/routes/search.py` - 集成V2服务，新增`name_weight`参数

### 测试脚本
- `scripts/test_name_weighting.py`

---

## 常见问题

**Q: 会影响现有搜索吗？**
A: 轻微影响。默认0.2权重只在名称匹配时才明显。

**Q: 如何禁用？**
A: 设置 `"name_weight": 0.0`

**Q: 如何调整权重？**
A: 在API请求中添加 `"name_weight": 0.3`（或其他值0.0-1.0）

**Q: 什么时候有效？**
A: LORA名称有意义时（如"cowgirl"）。随机编号（如"lora_001"）无效。

---

## 部署状态

✅ **已上线** (2026-03-13)

- 代码已部署
- API已重启
- 功能已测试通过

---

## 相关文档

- `SEARCH_MECHANISM_FAQ.md` - 搜索机制FAQ
