"""
改进的Embedding服务 - 支持名称相似度加权
"""
import asyncio
import logging
import time
from typing import List, Optional
import numpy as np
from sentence_transformers import SentenceTransformer
from pymilvus import connections, Collection, utility, CollectionSchema, FieldSchema, DataType

logger = logging.getLogger(__name__)


class EmbeddingServiceV2:
    """改进的Embedding服务 - 支持名称相似度加权"""

    def __init__(self, device: str = 'cpu'):
        logger.info(f"初始化Embedding服务V2，设备: {device}")
        self.model = SentenceTransformer('BAAI/bge-large-zh-v1.5', device=device)
        self.dimension = 1024
        self._connect_zilliz()
        self._init_collection()
        logger.info("Embedding服务V2初始化完成")

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
            # 创建新collection（与原版相同）
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
            index_params = {
                "metric_type": "IP",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128}
            }
            self.collection.create_index("embedding", index_params)
            logger.info(f"创建新collection: {collection_name}")

        self.collection.load()

    async def embed(self, text: str) -> List[float]:
        """生成单个文本的embedding"""
        embedding = await asyncio.to_thread(
            self.model.encode,
            text,
            normalize_embeddings=True
        )
        return embedding.tolist()

    def calculate_name_similarity(self, query: str, lora_name: str) -> float:
        """
        计算查询与LORA名称的相似度

        使用多种策略：
        1. 子串匹配
        2. 词重叠
        3. 语义相似度
        """
        query_lower = query.lower().replace(" ", "_")
        name_lower = lora_name.lower()

        # 策略1: 子串匹配（权重最高）
        if query_lower in name_lower:
            # 计算匹配程度
            match_ratio = len(query_lower) / len(name_lower)
            return 0.8 + match_ratio * 0.2  # 0.8-1.0

        # 策略2: 词重叠
        query_words = set(query.lower().split())
        name_words = set(name_lower.replace("_", " ").split())

        if query_words and name_words:
            overlap = len(query_words & name_words)
            union = len(query_words | name_words)
            jaccard = overlap / union if union > 0 else 0

            if jaccard > 0:
                return 0.5 + jaccard * 0.3  # 0.5-0.8

        # 策略3: 语义相似度（最慢，作为fallback）
        try:
            query_emb = self.model.encode(query, normalize_embeddings=True)
            name_emb = self.model.encode(lora_name.replace("_", " "), normalize_embeddings=True)
            semantic_sim = float(np.dot(query_emb, name_emb))
            return semantic_sim * 0.5  # 0-0.5
        except:
            return 0.0

    async def search_similar_loras_v2(
        self,
        query: str,
        lora_metadata: dict,  # {lora_id: {"name": "...", "category": "..."}}
        mode: Optional[str] = None,
        top_k: int = 10,
        name_weight: float = 0.3,  # 名称相似度权重
        min_similarity: float = 0.6  # 最低相似度阈值
    ) -> List[dict]:
        """
        改进的LORA搜索 - 结合标签相似度和名称相似度

        Args:
            query: 查询文本
            lora_metadata: LORA元数据字典
            mode: 模式筛选
            top_k: 返回数量
            name_weight: 名称相似度权重 (0-1)，默认0.3
            min_similarity: 最低相似度阈值 (0-1)，默认0.6，低于此值的结果会被过滤

        Returns:
            相似LORA列表，按综合分数排序
        """
        query_embedding = await self.embed(query)

        search_params = {"metric_type": "IP", "params": {"nprobe": 10}}

        # 只搜索 LORA 类型（向量中只有 enabled 的）
        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k * 2,
            expr='type == "lora"',
            output_fields=["lora_id", "prompt"]
        )

        # 按lora_id去重并聚合分数
        lora_scores = {}
        for hit in results[0]:
            lora_id = hit.entity.get("lora_id")
            tag_similarity = float(hit.distance)

            if lora_id not in lora_scores:
                lora_scores[lora_id] = {
                    "max_tag_similarity": tag_similarity,
                    "count": 1
                }
            else:
                lora_scores[lora_id]["max_tag_similarity"] = max(
                    lora_scores[lora_id]["max_tag_similarity"],
                    tag_similarity
                )
                lora_scores[lora_id]["count"] += 1

        # 按名称去重（同一LORA的high/low版本只保留一个）
        name_to_best_lora = {}
        for lora_id, scores in lora_scores.items():
            lora_name = lora_metadata.get(lora_id, {}).get("name", "")

            if lora_name not in name_to_best_lora:
                name_to_best_lora[lora_name] = {
                    "lora_id": lora_id,
                    "scores": scores
                }
            else:
                # 如果已存在同名LORA，保留相似度更高的
                existing_sim = name_to_best_lora[lora_name]["scores"]["max_tag_similarity"]
                if scores["max_tag_similarity"] > existing_sim:
                    name_to_best_lora[lora_name] = {
                        "lora_id": lora_id,
                        "scores": scores
                    }

        # 计算综合分数（标签相似度 + 名称相似度）
        final_scores = []
        for lora_name, data in name_to_best_lora.items():
            lora_id = data["lora_id"]
            scores = data["scores"]
            tag_sim = scores["max_tag_similarity"]

            # 计算名称相似度
            name_sim = self.calculate_name_similarity(query, lora_name)

            # 综合分数 = 标签相似度 * (1-name_weight) + 名称相似度 * name_weight
            final_score = tag_sim * (1 - name_weight) + name_sim * name_weight

            final_scores.append({
                "lora_id": lora_id,
                "similarity": final_score,
                "tag_similarity": tag_sim,
                "name_similarity": name_sim,
                "tag_count": scores["count"]
            })

            logger.debug(
                f"LORA {lora_id} ({lora_name}): "
                f"tag_sim={tag_sim:.3f}, name_sim={name_sim:.3f}, "
                f"final={final_score:.3f}"
            )

        # 过滤低于阈值的结果
        filtered_scores = [
            score for score in final_scores
            if score["similarity"] >= min_similarity
        ]

        logger.info(
            f"相似度过滤: 原始 {len(final_scores)} 个结果, "
            f"过滤后 {len(filtered_scores)} 个结果 (阈值: {min_similarity})"
        )

        # 排序：综合分数 > 标签数量 > LORA ID
        filtered_scores.sort(
            key=lambda x: (x["similarity"], x["tag_count"], -x["lora_id"]),
            reverse=True
        )

        return filtered_scores[:top_k]


# 全局实例
_embedding_service_v2: Optional[EmbeddingServiceV2] = None


def get_embedding_service_v2() -> EmbeddingServiceV2:
    """获取Embedding服务V2单例"""
    global _embedding_service_v2
    if _embedding_service_v2 is None:
        _embedding_service_v2 = EmbeddingServiceV2(device='cpu')
    return _embedding_service_v2
