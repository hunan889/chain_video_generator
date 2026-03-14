# 技术选型决策文档（使用Qwen本地模型）

## 一、Embedding模型选型（使用Qwen）

### 你们的环境
```bash
LLM_BASE_URL=http://127.0.0.1:20001/v1
LLM_MODEL=/home/gime/soft/Qwen3-14B-v2-Abliterated
```

### 问题：Qwen3-14B是对话模型，不是Embedding模型

**Qwen3-14B的作用**：
- ✅ 文本生成（对话、续写）
- ✅ 文本理解（分类、提取）
- ❌ **不能直接生成Embedding向量**

**Embedding模型的特点**：
- 专门训练用于将文本转换为固定维度的向量
- 输出是数值向量（如[0.1, 0.8, 0.3, ...]）
- 常见模型：text-embedding-3-large, bge-large-zh, gte-large等

### 解决方案：三种可行方案

---

## 方案A：使用Qwen本地Embedding模型 ⭐ **推荐**

### 部署Qwen2.5-Embedding模型

Qwen官方提供了专门的Embedding模型：
- **Qwen2.5-Embedding-7B**：7B参数，1024维向量
- **优点**：本地部署，无API成本，隐私安全
- **缺点**：需要额外GPU资源（约14GB显存）

### 部署方式

#### 方式1：使用vLLM部署（推荐）
```bash
# 1. 下载模型
cd /home/gime/soft
git clone https://www.modelscope.cn/Qwen/Qwen2.5-Embedding-7B.git

# 2. 启动vLLM服务（假设有空闲GPU）
CUDA_VISIBLE_DEVICES=3 python -m vllm.entrypoints.openai.api_server \
    --model /home/gime/soft/Qwen2.5-Embedding-7B \
    --port 20002 \
    --max-model-len 8192 \
    --trust-remote-code

# 3. 测试
curl http://127.0.0.1:20002/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen2.5-Embedding-7B",
    "input": "a woman in cowgirl position"
  }'
```

#### 方式2：使用Xinference部署
```bash
# 1. 安装Xinference
pip install xinference

# 2. 启动服务
xinference-local --host 0.0.0.0 --port 20002

# 3. 通过Web UI或API部署Qwen2.5-Embedding-7B
```

### 配置
```bash
# .env 添加
EMBEDDING_API_KEY=dummy
EMBEDDING_BASE_URL=http://127.0.0.1:20002/v1
EMBEDDING_MODEL=Qwen2.5-Embedding-7B
EMBEDDING_DIMENSION=1024
```

### 代码实现
```python
# api/services/embedding_service.py

import httpx
import numpy as np
from api.config import EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL

class QwenEmbeddingService:
    def __init__(self):
        self.api_key = EMBEDDING_API_KEY
        self.base_url = EMBEDDING_BASE_URL.rstrip("/")
        self.model = EMBEDDING_MODEL
        self.dimension = 1024

    async def embed(self, text: str) -> list[float]:
        """生成embedding"""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "input": text
                }
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def batch_embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成embedding"""
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "input": texts  # 批量处理
                }
            )
            response.raise_for_status()
            data = response.json()
            return [item["embedding"] for item in data["data"]]
```

### 性能评估
```
模型大小: 7B参数 (~14GB显存)
速度: ~50-100 tokens/s (单次embedding ~0.1-0.2s)
维度: 1024
质量: 对中英文支持良好，对NSFW术语理解中等
```

### Zilliz配置调整
```yaml
lora_recommendation:
  provider: milvus
  config:
    url: https://in01-4423417d207b120.gcp-us-west1.vectordb.zillizcloud.com
    token: cb7f6246ad28989f9ea2ea8b43a1bf0e263ebd44592e963772e603cb522caf53da7fccfb086a49e2147aa94f337f40975e61c1c5
    collection_name: wan22_lora_embeddings
    embedding_model_dims: 1024  # 改为1024维
    indexed_fields: ["resource_id", "lora_id", "type"]
```

---

## 方案B：使用BGE-Large-ZH（中文优化）

### 特点
- **BGE-Large-ZH-v1.5**：中文Embedding模型
- 维度：1024
- 大小：~1.3GB（小很多！）
- 可以用CPU运行（但慢）

### 部署方式
```bash
# 使用sentence-transformers
pip install sentence-transformers

# 下载模型（首次运行自动下载）
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-zh-v1.5')"
```

### 代码实现
```python
# api/services/embedding_service.py

from sentence_transformers import SentenceTransformer
import numpy as np

class BGEEmbeddingService:
    def __init__(self):
        # 加载模型到GPU（如果有）
        self.model = SentenceTransformer('BAAI/bge-large-zh-v1.5', device='cuda:3')
        self.dimension = 1024

    async def embed(self, text: str) -> list[float]:
        """生成embedding"""
        # sentence-transformers是同步的，用asyncio包装
        import asyncio
        embedding = await asyncio.to_thread(
            self.model.encode,
            text,
            normalize_embeddings=True
        )
        return embedding.tolist()

    async def batch_embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成embedding"""
        import asyncio
        embeddings = await asyncio.to_thread(
            self.model.encode,
            texts,
            normalize_embeddings=True,
            batch_size=32
        )
        return embeddings.tolist()
```

### 性能评估
```
模型大小: 1.3GB (~3GB显存)
速度:
  - GPU: ~0.02-0.05s/query
  - CPU: ~0.2-0.5s/query
维度: 1024
质量: 中文优秀，英文良好，NSFW术语理解中等
```

---

## 方案C：混合方案（LLM生成伪Embedding）⚠️ 不推荐

### 原理
使用Qwen3-14B生成文本的"特征向量"：
1. 让LLM提取文本的关键特征
2. 将特征转换为向量

### 问题
- ❌ 效果差：LLM不是为Embedding设计的
- ❌ 速度慢：每次需要完整的LLM推理
- ❌ 不稳定：同一文本可能生成不同向量

**不推荐使用此方案**

---

## 方案对比

| 方案 | 模型 | 显存 | 速度 | 质量 | 成本 | 推荐度 |
|------|------|------|------|------|------|--------|
| A | Qwen2.5-Embedding-7B | 14GB | 中 | 高 | 0 | ⭐⭐⭐⭐⭐ |
| B | BGE-Large-ZH | 3GB | 快 | 中高 | 0 | ⭐⭐⭐⭐ |
| C | Qwen3-14B伪Embedding | 28GB | 慢 | 低 | 0 | ⭐ |

---

## 最终推荐：方案A（Qwen2.5-Embedding-7B）

### 理由
1. **官方支持**：Qwen官方提供的Embedding模型，质量有保证
2. **本地部署**：无API成本，数据隐私安全
3. **效果好**：专门为Embedding任务训练
4. **可扩展**：支持批量处理，性能可接受

### 如果GPU资源紧张
选择**方案B（BGE-Large-ZH）**：
- 显存占用小（3GB vs 14GB）
- 可以用CPU运行（虽然慢但可用）
- 中文效果很好

---

## 实施步骤

### Step 1: 部署Embedding模型

#### 选项A：部署Qwen2.5-Embedding-7B
```bash
# 1. 检查GPU资源
nvidia-smi

# 2. 下载模型
cd /home/gime/soft
git clone https://www.modelscope.cn/Qwen/Qwen2.5-Embedding-7B.git

# 3. 启动服务（假设GPU 3空闲）
CUDA_VISIBLE_DEVICES=3 python -m vllm.entrypoints.openai.api_server \
    --model /home/gime/soft/Qwen2.5-Embedding-7B \
    --port 20002 \
    --max-model-len 8192 \
    --trust-remote-code

# 4. 测试
curl http://127.0.0.1:20002/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "Qwen2.5-Embedding-7B", "input": "test"}'
```

#### 选项B：使用BGE-Large-ZH（更轻量）
```bash
# 1. 安装依赖
pip install sentence-transformers

# 2. 下载模型（自动）
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-zh-v1.5')"

# 3. 无需启动服务，直接在代码中使用
```

### Step 2: 配置环境变量
```bash
# .env 添加
EMBEDDING_API_KEY=dummy
EMBEDDING_BASE_URL=http://127.0.0.1:20002/v1  # 如果用Qwen2.5-Embedding
EMBEDDING_MODEL=Qwen2.5-Embedding-7B
EMBEDDING_DIMENSION=1024
```

### Step 3: 实现Embedding服务
```python
# api/services/embedding_service.py

import httpx
import numpy as np
from pymilvus import connections, Collection, utility
from api.config import EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL

class EmbeddingService:
    def __init__(self):
        self.api_key = EMBEDDING_API_KEY
        self.base_url = EMBEDDING_BASE_URL.rstrip("/")
        self.model = EMBEDDING_MODEL
        self.dimension = 1024

        # 连接Zilliz
        connections.connect(
            alias="default",
            uri="https://in01-4423417d207b120.gcp-us-west1.vectordb.zillizcloud.com",
            token="cb7f6246ad28989f9ea2ea8b43a1bf0e263ebd44592e963772e603cb522caf53da7fccfb086a49e2147aa94f337f40975e61c1c5"
        )

        # 初始化collection
        self._init_collection()

    def _init_collection(self):
        """初始化Milvus collection"""
        from pymilvus import CollectionSchema, FieldSchema, DataType

        if utility.has_collection("wan22_lora_embeddings"):
            self.collection = Collection("wan22_lora_embeddings")
        else:
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024),  # 1024维
                FieldSchema(name="resource_id", dtype=DataType.INT64, nullable=True),
                FieldSchema(name="lora_id", dtype=DataType.INT64, nullable=True),
                FieldSchema(name="type", dtype=DataType.VARCHAR, max_length=20),
                FieldSchema(name="prompt", dtype=DataType.VARCHAR, max_length=2000),
                FieldSchema(name="created_at", dtype=DataType.INT64),
            ]

            schema = CollectionSchema(fields, description="LORA recommendation embeddings")
            self.collection = Collection("wan22_lora_embeddings", schema)

            # 创建索引
            index_params = {
                "metric_type": "IP",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128}
            }
            self.collection.create_index("embedding", index_params)

        self.collection.load()

    async def embed(self, text: str) -> list[float]:
        """生成embedding"""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "input": text
                }
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def batch_embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成embedding（提高效率）"""
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "input": texts
                }
            )
            response.raise_for_status()
            data = response.json()
            return [item["embedding"] for item in data["data"]]

    async def search_similar_resources(self, query: str, top_k: int = 10):
        """搜索相似资源"""
        query_embedding = await self.embed(query)

        search_params = {"metric_type": "IP", "params": {"nprobe": 10}}

        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr='type == "resource"',
            output_fields=["resource_id", "prompt"]
        )

        items = []
        for hit in results[0]:
            items.append({
                "resource_id": hit.entity.get("resource_id"),
                "prompt": hit.entity.get("prompt"),
                "similarity": float(hit.distance)
            })

        return items

    async def search_similar_loras(self, query: str, top_k: int = 10):
        """搜索相似LORA"""
        query_embedding = await self.embed(query)

        search_params = {"metric_type": "IP", "params": {"nprobe": 10}}

        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k * 3,
            expr='type == "lora"',
            output_fields=["lora_id", "prompt"]
        )

        # 按lora_id去重并聚合分数
        lora_scores = {}
        for hit in results[0]:
            lora_id = hit.entity.get("lora_id")
            score = float(hit.distance)

            if lora_id not in lora_scores:
                lora_scores[lora_id] = {"max_score": score, "count": 1}
            else:
                lora_scores[lora_id]["max_score"] = max(lora_scores[lora_id]["max_score"], score)
                lora_scores[lora_id]["count"] += 1

        sorted_loras = sorted(lora_scores.items(), key=lambda x: x[1]["max_score"], reverse=True)[:top_k]

        return [{"lora_id": lora_id, "similarity": data["max_score"]} for lora_id, data in sorted_loras]
```

### Step 4: 测试
```python
# 测试脚本
import asyncio
from api.services.embedding_service import EmbeddingService

async def test():
    service = EmbeddingService()

    # 测试embedding生成
    embedding = await service.embed("a woman in cowgirl position")
    print(f"Embedding dimension: {len(embedding)}")
    print(f"First 10 values: {embedding[:10]}")

    # 测试相似度
    emb1 = await service.embed("cowgirl position")
    emb2 = await service.embed("woman on top")
    emb3 = await service.embed("doggy style")

    import numpy as np
    def cosine_similarity(a, b):
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

    print(f"cowgirl vs woman on top: {cosine_similarity(emb1, emb2):.3f}")
    print(f"cowgirl vs doggy style: {cosine_similarity(emb1, emb3):.3f}")

asyncio.run(test())
```

---

## 完整技术栈（最终版）

| 组件 | 方案 | 说明 |
|------|------|------|
| **Embedding** | Qwen2.5-Embedding-7B (1024维) | 本地部署，无API成本 |
| **向量检索** | Zilliz (Milvus) | 复用已有基础设施 |
| **异步任务** | TaskManager + asyncio | 复用现有架构 |
| **数据存储** | MySQL (元数据) + Zilliz (向量) | 混合存储 |
| **LLM** | Qwen3-14B (已有) | 用于LORA分类辅助 |

**新增依赖**：
```bash
pip install pymilvus
# 如果用BGE: pip install sentence-transformers
```

---

## GPU资源分配建议

```
GPU 0: ComfyUI A14B (已占用)
GPU 1: ComfyUI A14B (已占用)
GPU 2: ComfyUI 5B (已占用)
GPU 3: Qwen2.5-Embedding-7B (新增) ← 推荐
```

如果GPU 3也被占用，可以：
1. 使用BGE-Large-ZH（只需3GB显存，可以和其他模型共享GPU）
2. 使用CPU运行BGE（慢但可用）

---

## 总结

✅ **推荐方案**：Qwen2.5-Embedding-7B + Zilliz
- 本地部署，无API成本
- 效果好，支持中英文
- 复用已有Zilliz基础设施

✅ **备选方案**：BGE-Large-ZH + Zilliz
- 显存占用小（3GB）
- 可以CPU运行
- 中文效果优秀

**下一步**：确认GPU资源情况，选择部署方案，开始实施！
