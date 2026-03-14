# 🚀 Embedding服务快速开始

## ✅ 当前状态

Embedding服务已部署完成，可以立即使用！

---

## 🎯 快速测试

### 1. 基础功能测试
```bash
cd /home/gime/soft/wan22-service
source /home/gime/soft/miniconda3/bin/activate llm
python scripts/test_embedding_service.py
```

### 2. 场景演示
```bash
python scripts/demo_embedding.py
```

---

## 💻 在代码中使用

### 基础用法
```python
from api.services.embedding_service import get_embedding_service

# 获取服务实例（单例）
service = get_embedding_service()

# 生成单个embedding
embedding = await service.embed("a woman in cowgirl position")
print(f"维度: {len(embedding)}")  # 1024

# 批量生成embedding（更高效）
texts = ["cowgirl", "doggy style", "missionary"]
embeddings = await service.batch_embed(texts)
```

### 语义搜索
```python
# 搜索相似资源（从收藏中）
results = await service.search_similar_resources(
    query="girl riding on top",
    top_k=10
)

for item in results:
    print(f"资源ID: {item['resource_id']}")
    print(f"Prompt: {item['prompt']}")
    print(f"相似度: {item['similarity']:.3f}")
```

### 索引管理
```python
# 为资源建立索引
await service.index_resource(
    resource_id=123,
    prompt="a sexy woman in cowgirl position"
)

# 为LORA建立索引
await service.index_lora(
    lora_id=45,
    example_prompts=[
        "cowgirl position sex",
        "woman riding on top",
        "bouncing on dick"
    ]
)

# 删除索引
await service.delete_resource_index(resource_id=123)
await service.delete_lora_index(lora_id=45)
```

---

## 📊 性能建议

### GPU vs CPU
```python
# 使用GPU（推荐，快10倍）
service = EmbeddingService(device='cuda:3')

# 使用CPU（备用）
service = EmbeddingService(device='cpu')
```

### 批量处理
```python
# ❌ 不推荐：逐个处理
for text in texts:
    emb = await service.embed(text)

# ✅ 推荐：批量处理
embeddings = await service.batch_embed(texts)
```

---

## 🔧 配置

### 环境变量（.env）
```bash
EMBEDDING_MODEL=bge-large-zh
EMBEDDING_DIMENSION=1024
EMBEDDING_DEVICE=cuda:3
```

### Zilliz配置
已内置在代码中，无需额外配置。

---

## 📝 下一步

1. **同步LORA数据**: 将loras.yaml同步到数据库
2. **建立初始索引**: 为收藏的资源和LORA建立索引
3. **开发API**: 实现搜索和推荐API
4. **前端集成**: 添加智能推荐面板

---

## 🆘 常见问题

### Q: 首次使用很慢？
A: 首次加载模型需要5-10秒，之后会很快。

### Q: GPU内存不足？
A: 改用CPU或其他GPU：`EmbeddingService(device='cpu')`

### Q: 如何查看索引统计？
A: `stats = await service.get_stats()`

---

## 📚 完整文档

- [部署报告](./EMBEDDING_DEPLOYMENT_REPORT.md)
- [部署总结](./EMBEDDING_DEPLOYMENT_SUMMARY.md)
- [技术选型](./TECH_STACK_DECISION_QWEN.md)
- [系统设计](./LORA_RECOMMENDATION_DESIGN.md)

---

**准备就绪！开始使用吧！** 🎉
