"""
Embedding服务 - 使用BGE-Large-ZH模型
"""
import asyncio
import logging
import time
from typing import List, Optional
import numpy as np
from sentence_transformers import SentenceTransformer
from pymilvus import connections, Collection, utility, CollectionSchema, FieldSchema, DataType

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Embedding服务 - 使用BGE-Large-ZH模型"""

    def __init__(self, device: str = 'cpu'):
        """
        初始化Embedding服务

        Args:
            device: 设备 ('cuda:0', 'cuda:1', 'cpu' 等)
        """
        logger.info(f"初始化Embedding服务，设备: {device}")

        # 加载模型
        self.model = SentenceTransformer('BAAI/bge-large-zh-v1.5', device=device)
        self.dimension = 1024

        # 连接Zilliz
        self._connect_zilliz()

        # 初始化collection
        self._init_collection()

        logger.info("Embedding服务初始化完成")

    def _connect_zilliz(self):
        """连接Zilliz云服务"""
        connections.connect(
            alias="default",
            uri="https://in01-4423417d207b120.gcp-us-west1.vectordb.zillizcloud.com",
            token="cb7f6246ad28989f9ea2ea8b43a1bf0e263ebd44592e963772e603cb522caf53da7fccfb086a49e2147aa94f337f40975e61c1c5"
        )
        logger.info("已连接到Zilliz")

    def _init_collection(self):
        """初始化Milvus collection"""
        collection_name = "wan22_lora_embeddings"

        if utility.has_collection(collection_name):
            self.collection = Collection(collection_name)
            logger.info(f"加载已存在的collection: {collection_name}")
        else:
            # 创建新collection
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024),
                FieldSchema(name="resource_id", dtype=DataType.INT64, nullable=True),
                FieldSchema(name="lora_id", dtype=DataType.INT64, nullable=True),
                FieldSchema(name="type", dtype=DataType.VARCHAR, max_length=20),
                FieldSchema(name="prompt", dtype=DataType.VARCHAR, max_length=2000),
                FieldSchema(name="enabled", dtype=DataType.BOOL, default_value=True),
                FieldSchema(name="created_at", dtype=DataType.INT64),
            ]

            schema = CollectionSchema(fields, description="LORA recommendation embeddings")
            self.collection = Collection(collection_name, schema)

            # 创建索引
            index_params = {
                "metric_type": "IP",  # 内积（余弦相似度）
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128}
            }
            self.collection.create_index("embedding", index_params)
            logger.info(f"创建新collection: {collection_name}")

        # 加载到内存
        self.collection.load()

    async def embed(self, text: str) -> List[float]:
        """
        生成单个文本的embedding

        Args:
            text: 输入文本

        Returns:
            embedding向量
        """
        embedding = await asyncio.to_thread(
            self.model.encode,
            text,
            normalize_embeddings=True
        )
        return embedding.tolist()

    async def batch_embed(self, texts: List[str]) -> List[List[float]]:
        """
        批量生成embedding

        Args:
            texts: 文本列表

        Returns:
            embedding向量列表
        """
        embeddings = await asyncio.to_thread(
            self.model.encode,
            texts,
            normalize_embeddings=True,
            batch_size=32
        )
        return embeddings.tolist()

    async def index_resource(self, resource_id: int, prompt: str, enabled: bool = True):
        """
        为资源建立索引

        Args:
            resource_id: 资源ID
            prompt: 资源的prompt
            enabled: 是否启用（是否被收藏）
        """
        embedding = await self.embed(prompt)

        data = [{
            "embedding": embedding,
            "resource_id": resource_id,
            "lora_id": None,
            "type": "resource",
            "prompt": prompt,
            "enabled": enabled,
            "created_at": int(time.time())
        }]

        self.collection.insert(data)
        self.collection.flush()
        logger.info(f"已为资源 {resource_id} 建立索引 (enabled={enabled})")

    async def index_lora(self, lora_id: int, example_prompts: List[str], enabled: bool = True):
        """
        为LORA建立索引（基于example_prompts）

        Args:
            lora_id: LORA ID
            example_prompts: 示例prompt列表
            enabled: 是否启用
        """
        if not example_prompts:
            logger.warning(f"LORA {lora_id} 没有example_prompts，跳过索引")
            return

        embeddings = await self.batch_embed(example_prompts)

        data = []
        for prompt, embedding in zip(example_prompts, embeddings):
            data.append({
                "embedding": embedding,
                "resource_id": None,
                "lora_id": lora_id,
                "type": "lora",
                "prompt": prompt,
                "enabled": enabled,
                "created_at": int(time.time())
            })

        self.collection.insert(data)
        self.collection.flush()
        logger.info(f"已为LORA {lora_id} 建立 {len(example_prompts)} 个索引 (enabled={enabled})")

    async def index_image_lora(self, image_lora_id: str, trigger_prompt: str, tags: List[str] = None):
        """
        为图片LORA建立索引

        Args:
            image_lora_id: 图片LORA ID (string)
            trigger_prompt: 触发词prompt
            tags: 标签列表
        """
        # 构建索引文本
        texts = []
        if trigger_prompt:
            texts.append(trigger_prompt)
        if tags:
            texts.extend(tags)

        if not texts:
            logger.warning(f"Image LORA {image_lora_id} 没有可索引的文本，跳过")
            return

        # 生成embeddings
        embeddings = await self.batch_embed(texts)

        # 插入数据
        data = []
        for text, embedding in zip(texts, embeddings):
            data.append({
                "embedding": embedding,
                "resource_id": None,
                "lora_id": None,  # image_lora uses string ID, stored in prompt field
                "type": "image_lora",
                "prompt": f"image_lora:{image_lora_id}:{text}",
                "created_at": int(time.time())
            })

        self.collection.insert(data)
        self.collection.flush()
        logger.info(f"已为Image LORA {image_lora_id} 建立 {len(texts)} 个索引")


    async def search_similar_resources(self, query: str, top_k: int = 10) -> List[dict]:
        """
        搜索相似资源

        Args:
            query: 查询文本
            top_k: 返回top-k个结果

        Returns:
            相似资源列表
        """
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

        logger.info(f"搜索到 {len(items)} 个相似资源")
        return items

    async def search_similar_loras(self, query: str, mode: Optional[str] = None, top_k: int = 10) -> List[dict]:
        """
        搜索相似LORA

        Args:
            query: 查询文本
            mode: 模式筛选 (I2V, T2V, both, None表示不筛选)
            top_k: 返回top-k个结果

        Returns:
            相似LORA列表
        """
        query_embedding = await self.embed(query)

        search_params = {"metric_type": "IP", "params": {"nprobe": 10}}

        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k * 3,  # 多取一些，因为一个LORA可能有多个example_prompts
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

        # 按最高分排序，相同时按匹配数量、LORA ID排序
        sorted_loras = sorted(
            lora_scores.items(),
            key=lambda x: (
                x[1]["max_score"],      # 主排序：相似度（越高越好）
                x[1]["count"],          # 次排序：匹配标签数量（越多越好）
                -x[0]                   # 三级排序：LORA ID（新的优先）
            ),
            reverse=True
        )[:top_k]

        items = [{"lora_id": lora_id, "similarity": data["max_score"]} for lora_id, data in sorted_loras]

        logger.info(f"搜索到 {len(items)} 个相似LORA")
        return items

    async def search_similar_image_loras(self, query: str, top_k: int = 10) -> List[dict]:
        """
        搜索相似图片LORA

        Args:
            query: 查询文本
            top_k: 返回top-k个结果

        Returns:
            相似图片LORA列表，格式: [{"image_lora_id": "xxx", "similarity": 0.85}, ...]
        """
        query_embedding = await self.embed(query)

        search_params = {"metric_type": "IP", "params": {"nprobe": 10}}

        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k * 3,  # 多取一些，因为一个image LORA可能有多个tags
            expr='type == "image_lora"',
            output_fields=["prompt"]
        )

        # 从prompt字段解析image_lora_id: "image_lora:{id}:{text}"
        lora_scores = {}
        for hit in results[0]:
            prompt = hit.entity.get("prompt", "")
            score = float(hit.distance)

            # 解析格式: "image_lora:{id}:{text}"
            if prompt.startswith("image_lora:"):
                parts = prompt.split(":", 2)
                if len(parts) >= 2:
                    image_lora_id = parts[1]

                    if image_lora_id not in lora_scores:
                        lora_scores[image_lora_id] = {"max_score": score, "count": 1}
                    else:
                        lora_scores[image_lora_id]["max_score"] = max(lora_scores[image_lora_id]["max_score"], score)
                        lora_scores[image_lora_id]["count"] += 1

        # 按最高分排序
        sorted_loras = sorted(
            lora_scores.items(),
            key=lambda x: (
                x[1]["max_score"],
                x[1]["count"],
                x[0]
            ),
            reverse=True
        )[:top_k]

        items = [{"image_lora_id": lora_id, "similarity": data["max_score"]} for lora_id, data in sorted_loras]

        logger.info(f"搜索到 {len(items)} 个相似Image LORA")
        return items

    async def delete_resource_index(self, resource_id: int):
        """删除资源索引"""
        self.collection.delete(expr=f'resource_id == {resource_id}')
        logger.info(f"已删除资源 {resource_id} 的索引")

    async def delete_lora_index(self, lora_id: int):
        """删除LORA索引"""
        self.collection.delete(expr=f'lora_id == {lora_id}')
        logger.info(f"已删除LORA {lora_id} 的索引")

    async def get_stats(self) -> dict:
        """获取索引统计信息"""
        await asyncio.to_thread(self.collection.load)
        total = self.collection.num_entities

        # 获取分类统计
        resource_count = 0
        lora_count = 0

        try:
            # 查询resources数量
            res_results = self.collection.query(
                expr="type == 'resource'",
                output_fields=["resource_id"],
                limit=16384
            )
            resource_count = len(res_results)

            # 查询loras数量
            lora_results = self.collection.query(
                expr="type == 'lora'",
                output_fields=["lora_id"],
                limit=16384
            )
            lora_count = len(lora_results)
        except Exception as e:
            logger.warning(f"Failed to get detailed stats: {e}")

        return {
            "total_count": total,
            "resource_count": resource_count,
            "lora_count": lora_count,
            "model": "BGE-Large-ZH-v1.5",
            "dimension": 1024,
            "collection": "wan22_lora_embeddings",
            "device": str(self.model.device)
        }


# 全局实例
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """获取Embedding服务单例"""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService(device='cpu')
    return _embedding_service
