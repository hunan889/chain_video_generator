# LORA推荐系统优化建议

## 1. 性能优化

### 1.1 GPU加速 (高优先级)
**问题**: CPU模式下embedding生成速度慢（2-3秒/个）

**解决方案**:
```python
# api/services/embedding_service.py
# 根据可用GPU动态选择设备
import torch

def get_available_device():
    if torch.cuda.is_available():
        # 选择空闲的GPU
        for i in range(torch.cuda.device_count()):
            if i not in [0, 1]:  # 避免与ComfyUI冲突
                return f'cuda:{i}'
    return 'cpu'

# 在get_embedding_service()中使用
_embedding_service = EmbeddingService(device=get_available_device())
```

**预期提升**: 10-20倍速度提升

### 1.2 批量Embedding生成
**问题**: 索引构建时逐个生成embedding效率低

**解决方案**:
```python
async def batch_embed(self, texts: List[str], batch_size: int = 32) -> List[np.ndarray]:
    """批量生成embedding"""
    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        batch_embeddings = await asyncio.to_thread(
            self.model.encode, batch, normalize_embeddings=True
        )
        embeddings.extend(batch_embeddings)
    return embeddings
```

**预期提升**: 2-3倍速度提升

### 1.3 Embedding缓存
**问题**: 相同查询重复计算embedding

**解决方案**:
```python
from functools import lru_cache
import hashlib

class EmbeddingService:
    def __init__(self):
        self.cache = {}

    async def embed(self, text: str) -> np.ndarray:
        cache_key = hashlib.md5(text.encode()).hexdigest()
        if cache_key in self.cache:
            return self.cache[cache_key]

        embedding = await asyncio.to_thread(
            self.model.encode, text, normalize_embeddings=True
        )
        self.cache[cache_key] = embedding
        return embedding
```

### 1.4 数据库查询优化
**问题**: 搜索后需要额外查询数据库获取完整信息

**解决方案**:
- 在Milvus中存储更多字段（name, description等）
- 减少数据库往返次数
- 使用连接池

## 2. 用户体验优化

### 2.1 实时进度推送
**问题**: 前端需要轮询任务状态

**解决方案**: 使用WebSocket推送进度
```python
# api/routes/embeddings.py
from fastapi import WebSocket

@router.websocket("/admin/embeddings/ws/{task_id}")
async def task_progress_ws(websocket: WebSocket, task_id: str):
    await websocket.accept()
    while True:
        task = _index_tasks.get(task_id)
        if task:
            await websocket.send_json(task)
            if task['status'] in ['completed', 'failed']:
                break
        await asyncio.sleep(1)
    await websocket.close()
```

### 2.2 推荐结果预览
**问题**: 用户无法预览LORA效果

**解决方案**:
- 在推荐卡片中显示preview_url视频
- 添加hover预览功能
- 支持点击查看大图/视频

### 2.3 智能触发词合并
**问题**: 多个LORA的触发词可能重复或冲突

**解决方案**:
```python
def merge_trigger_words(loras: List[dict], existing_prompt: str) -> str:
    """智能合并触发词，避免重复"""
    words = set()
    for lora in loras:
        for word in lora['trigger_words']:
            word_lower = word.lower()
            # 检查是否已存在
            if word_lower not in existing_prompt.lower():
                words.add(word)

    return existing_prompt + ', ' + ', '.join(words)
```

### 2.4 推荐解释
**问题**: 用户不知道为什么推荐这些LORA

**解决方案**:
- 显示相似度分数
- 显示匹配的关键词
- 添加"为什么推荐"说明

## 3. 功能增强

### 3.1 增量索引更新
**问题**: 每次重建索引需要处理所有数据

**解决方案**:
```python
async def index_new_lora(self, lora_id: int):
    """为单个新LORA建立索引"""
    # 检查是否已存在
    existing = self.collection.query(
        expr=f'lora_id == {lora_id}',
        output_fields=["id"]
    )

    if existing:
        # 删除旧索引
        self.collection.delete(expr=f'lora_id == {lora_id}')

    # 添加新索引
    await self._index_single_lora(lora_id)
```

### 3.2 个性化推荐
**问题**: 推荐结果不考虑用户偏好

**解决方案**:
- 记录用户使用历史
- 基于协同过滤推荐
- 学习用户偏好权重

### 3.3 LORA组合推荐
**问题**: 只推荐单个LORA，不考虑组合效果

**解决方案**:
- 分析LORA共现模式
- 推荐常用组合
- 检测冲突组合并警告

### 3.4 Prompt优化集成
**问题**: 推荐和Prompt优化功能分离

**解决方案**:
```python
@router.post("/recommend-and-optimize")
async def recommend_and_optimize(req: RecommendRequest):
    """推荐LORA并优化prompt"""
    # 1. 获取推荐
    recommendations = await get_recommendations(req.prompt)

    # 2. 基于推荐的LORA优化prompt
    optimized_prompt = await optimize_prompt_with_loras(
        req.prompt,
        recommendations['loras']
    )

    return {
        'images': recommendations['images'],
        'loras': recommendations['loras'],
        'optimized_prompt': optimized_prompt
    }
```

## 4. 代码质量优化

### 4.1 错误处理增强
```python
# 统一错误处理装饰器
def handle_api_errors(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"API error in {func.__name__}: {e}", exc_info=True)
            raise HTTPException(500, f"Internal error: {str(e)}")
    return wrapper

@router.post("/search/loras")
@handle_api_errors
async def search_similar_loras(req: SearchLorasRequest):
    ...
```

### 4.2 配置管理
**问题**: 硬编码的配置分散在代码中

**解决方案**:
```python
# api/config.py
class EmbeddingConfig:
    MODEL_NAME = "BAAI/bge-large-zh-v1.5"
    DIMENSION = 1024
    DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")
    BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
    CACHE_SIZE = int(os.getenv("EMBEDDING_CACHE_SIZE", "1000"))

class ZillizConfig:
    URI = os.getenv("ZILLIZ_URI")
    TOKEN = os.getenv("ZILLIZ_TOKEN")
    COLLECTION_NAME = "wan22_lora_embeddings"
```

### 4.3 单元测试
```python
# tests/test_embedding_service.py
import pytest
from api.services.embedding_service import EmbeddingService

@pytest.fixture
def embedding_service():
    return EmbeddingService(device='cpu')

async def test_embed(embedding_service):
    text = "a woman in sexy pose"
    embedding = await embedding_service.embed(text)
    assert embedding.shape == (1024,)
    assert -1 <= embedding.min() <= 1
    assert -1 <= embedding.max() <= 1

async def test_search_similar_loras(embedding_service):
    results = await embedding_service.search_similar_loras(
        query="orgasm",
        top_k=5
    )
    assert isinstance(results, list)
    assert len(results) <= 5
```

## 5. 监控和日志

### 5.1 性能监控
```python
import time
from functools import wraps

def monitor_performance(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.time()
        result = await func(*args, **kwargs)
        duration = time.time() - start

        logger.info(f"{func.__name__} took {duration:.2f}s")

        # 记录到监控系统
        # metrics.record(f"api.{func.__name__}.duration", duration)

        return result
    return wrapper
```

### 5.2 搜索日志分析
```python
# 记录搜索查询用于分析
async def log_search_query(query: str, results: List[dict]):
    await db.execute("""
        INSERT INTO search_logs (query, result_count, timestamp)
        VALUES (?, ?, ?)
    """, (query, len(results), datetime.now()))
```

## 6. 部署优化

### 6.1 Docker化
```dockerfile
# Dockerfile
FROM python:3.11-slim

# 安装依赖
COPY requirements.txt .
RUN pip install -r requirements.txt

# 下载模型（构建时）
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-zh-v1.5')"

# 复制代码
COPY . /app
WORKDIR /app

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 6.2 负载均衡
- 使用Nginx反向代理
- 多实例部署
- 共享Zilliz向量数据库

### 6.3 健康检查
```python
@app.get("/health/embedding")
async def embedding_health():
    try:
        service = get_embedding_service()
        # 测试embedding生成
        await service.embed("test")
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}
```

## 实施优先级

### P0 (立即实施)
1. ✅ 修复CUDA设备配置
2. ✅ 修复search_similar_loras参数
3. ⏳ GPU加速配置

### P1 (本周完成)
1. 批量embedding生成
2. 错误处理增强
3. 配置管理优化
4. 空索引友好提示

### P2 (下周完成)
1. Embedding缓存
2. WebSocket进度推送
3. 推荐结果预览
4. 增量索引更新

### P3 (后续迭代)
1. 个性化推荐
2. LORA组合推荐
3. 监控和日志系统
4. 单元测试覆盖

## 预期效果

实施以上优化后：
- **性能**: 索引构建速度提升20-30倍（GPU + 批量处理）
- **体验**: 实时进度反馈，推荐结果更直观
- **稳定性**: 完善的错误处理和监控
- **可维护性**: 清晰的配置管理和测试覆盖
