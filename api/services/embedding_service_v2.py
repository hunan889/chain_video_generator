"""
改进的Embedding服务 - 支持名称相似度加权和关键词加权
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

    def _calculate_keyword_score(self, query: str, search_keywords: str, feature_keywords: List[str]) -> float:
        """
        计算关键词匹配得分（考虑位置和突出程度）
        与 embedding_service.py 中的实现相同

        Args:
            query: 查询文本
            search_keywords: LORA的search_keywords
            feature_keywords: 特征关键词列表

        Returns:
            关键词匹配得分 (0-1)
        """
        from collections import Counter

        query_lower = query.lower()
        keywords_lower = search_keywords.lower()

        total_score = 0.0
        total_keywords_in_query = 0

        for keyword in feature_keywords:
            keyword_lower = keyword.lower()

            # 完整短语匹配
            has_match = keyword_lower in query_lower

            # 多词短语：所有词都在查询中（考虑词频）
            if not has_match and ' ' in keyword_lower:
                keyword_words = keyword_lower.split()
                query_words = query_lower.split()

                # 统计词频
                keyword_counter = Counter(keyword_words)
                query_counter = Counter(query_words)

                # 检查查询中是否包含关键词的所有词（考虑重复）
                if all(query_counter[kw] >= count for kw, count in keyword_counter.items()):
                    has_match = True
                else:
                    # 部分匹配：至少匹配 60% 的词（用于处理 "on her fours" vs "on all fours"）
                    matched_words = sum(1 for kw in keyword_counter if query_counter[kw] > 0)
                    if len(keyword_words) >= 3 and matched_words >= len(keyword_words) * 0.6:
                        has_match = True

            if has_match:
                total_keywords_in_query += 1

                # 检查关键词是否在search_keywords中
                match_keyword = keyword_lower
                if match_keyword not in keywords_lower:
                    # 对于多词短语，如果整个短语不在，就跳过（不降级到单词匹配）
                    # 避免 "on all fours" 匹配到 "on" 这种误匹配
                    continue

                # 基础匹配得分
                base_score = 1.0

                # 位置加权：关键词出现在前面得分更高（进一步减少位置影响）
                position = keywords_lower.find(match_keyword)
                keywords_length = len(keywords_lower)
                # 从 0.3 改为 0.2，进一步减少位置的惩罚
                position_weight = 1.0 - (position / keywords_length) * 0.2 if keywords_length > 0 else 1.0

                # 突出程度：检查是否在开头（前50个字符）
                prominence_weight = 1.0
                if position < 50:
                    prominence_weight += 0.5  # 从0.3提升到0.5

                # 检查关键词出现次数
                count = keywords_lower.count(match_keyword)
                count_weight = min(1.0 + (count - 1) * 0.2, 1.5)

                # 综合得分
                keyword_score = base_score * position_weight * prominence_weight * count_weight
                total_score += min(keyword_score, 2.0)

        if total_keywords_in_query == 0:
            return 0.0

        # 归一化到 0-1
        # 不除以 total_keywords_in_query，避免被查询中的多个关键词稀释
        return min(total_score / 2.0, 1.0)

    def _extract_query_keywords(self, query: str, feature_keywords: List[str]) -> List[str]:
        """
        从查询中提取匹配的特征关键词

        Args:
            query: 查询文本
            feature_keywords: 特征关键词列表

        Returns:
            匹配的关键词列表
        """
        from collections import Counter

        query_lower = query.lower()
        matched_keywords = []

        for keyword in feature_keywords:
            keyword_lower = keyword.lower()

            # 完整短语匹配（优先级最高）
            has_match = keyword_lower in query_lower

            # 多词短语：检查词序和邻近性
            if not has_match and ' ' in keyword_lower:
                keyword_words = keyword_lower.split()
                query_words = query_lower.split()

                # 统计词频
                keyword_counter = Counter(keyword_words)
                query_counter = Counter(query_words)

                # 检查查询中是否包含关键词的所有词（考虑重复）
                if all(query_counter[kw] >= count for kw, count in keyword_counter.items()):
                    has_match = True
                else:
                    # 部分匹配：要求词按顺序出现且相邻或接近（最多间隔1个词）
                    # 用于处理 "on her fours" vs "on all fours"
                    # 要求：至少匹配60%的词，且必须包含第一个和最后一个词
                    matched_words = sum(1 for kw in keyword_counter if query_counter[kw] > 0)
                    if len(keyword_words) >= 3 and matched_words >= len(keyword_words) * 0.6:
                        # 找到关键词中每个词在查询中的位置（按顺序）
                        positions = []
                        query_idx = 0
                        for kw_word in keyword_words:
                            found = False
                            for i in range(query_idx, len(query_words)):
                                if query_words[i] == kw_word:
                                    positions.append(i)
                                    query_idx = i + 1
                                    found = True
                                    break
                            if not found:
                                positions.append(-1)  # 未找到

                        # 检查是否有足够的词按顺序找到，且间隔不超过2
                        # 并且必须包含第一个和最后一个词
                        valid_positions = [p for p in positions if p >= 0]
                        if (len(valid_positions) >= len(keyword_words) * 0.6 and
                            positions[0] >= 0 and positions[-1] >= 0):  # 第一个和最后一个词必须存在
                            max_gap = max(valid_positions[i+1] - valid_positions[i] for i in range(len(valid_positions)-1)) if len(valid_positions) > 1 else 0
                            if max_gap <= 2:  # 允许最多间隔1个词
                                has_match = True

            if has_match:
                matched_keywords.append(keyword)

        return matched_keywords

    def _get_keyword_variants(self, keywords: List[str]) -> List[str]:
        """
        获取关键词的所有变体（包括近义词和相关词）

        Args:
            keywords: 关键词列表

        Returns:
            所有变体列表
        """
        from api.services.embedding_service import KEYWORD_SYNONYMS

        variants = set(keywords)

        for keyword in keywords:
            keyword_lower = keyword.lower()

            # 查找该关键词属于哪个主关键词的变体
            for main_keyword, config in KEYWORD_SYNONYMS.items():
                all_variants = [main_keyword] + config.get('synonyms', []) + config.get('related_words', [])

                # 如果查询关键词是某个主关键词的变体
                if any(variant.lower() == keyword_lower or
                       variant.lower() in keyword_lower or
                       keyword_lower in variant.lower()
                       for variant in all_variants):
                    # 添加所有变体
                    variants.update(all_variants)
                    break

        return list(variants)

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
        min_similarity: float = 0.6,  # 最低相似度阈值
        keyword_boost: float = 0.0,  # 关键词加权
        feature_keywords: Optional[List[str]] = None,  # 特征关键词列表
        lora_search_keywords: Optional[dict] = None  # {lora_id: "search_keywords"}
    ) -> List[dict]:
        """
        改进的LORA搜索 - 混合召回（向量召回 + 关键词召回）

        Args:
            query: 查询文本
            lora_metadata: LORA元数据字典
            mode: 模式筛选
            top_k: 返回数量
            name_weight: 名称相似度权重 (0-1)，默认0.3
            min_similarity: 最低相似度阈值 (0-1)，默认0.6，低于此值的结果会被过滤
            keyword_boost: 关键词加权 (0-1)，默认0.0
            feature_keywords: 特征关键词列表
            lora_search_keywords: LORA的search_keywords字典

        Returns:
            相似LORA列表，按综合分数排序
        """
        logger.info(f"[LORA搜索] 查询: {query}, keyword_boost={keyword_boost}, feature_keywords数量={len(feature_keywords) if feature_keywords else 0}, lora_search_keywords数量={len(lora_search_keywords) if lora_search_keywords else 0}")

        # ========== 第一阶段：向量召回 ==========
        query_embedding = await self.embed(query)

        search_params = {"metric_type": "IP", "params": {"ef": 64}}

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
        vector_recall_loras = {}
        for hit in results[0]:
            lora_id = hit.entity.get("lora_id")
            tag_similarity = float(hit.distance)

            if lora_id not in vector_recall_loras:
                vector_recall_loras[lora_id] = {
                    "max_tag_similarity": tag_similarity,
                    "count": 1
                }
            else:
                vector_recall_loras[lora_id]["max_tag_similarity"] = max(
                    vector_recall_loras[lora_id]["max_tag_similarity"],
                    tag_similarity
                )
                vector_recall_loras[lora_id]["count"] += 1

        logger.info(f"向量召回: {len(vector_recall_loras)} 个LORA")

        # ========== 第二阶段：关键词召回 ==========
        keyword_recall_loras = set()
        query_keywords = []  # 初始化为空列表

        if keyword_boost > 0 and feature_keywords and lora_search_keywords:
            # 提取查询中的关键词
            query_keywords = self._extract_query_keywords(query, feature_keywords)

            if query_keywords:
                # 获取关键词的所有变体（同义词、相关词）
                keyword_variants = self._get_keyword_variants(query_keywords)

                logger.info(f"[LORA混合召回] 查询: {query}")
                logger.info(f"[LORA混合召回] 查询关键词: {query_keywords}")
                logger.info(f"[LORA混合召回] 关键词变体: {keyword_variants}")

                # 在LORA的search_keywords中查找匹配
                for lora_id, keywords_text in lora_search_keywords.items():
                    keywords_lower = keywords_text.lower()

                    for variant in keyword_variants:
                        if variant.lower() in keywords_lower:
                            keyword_recall_loras.add(lora_id)
                            logger.debug(f"[LORA混合召回] LORA {lora_id} 匹配变体: {variant}")
                            break

                logger.info(f"[LORA混合召回] 关键词召回: {len(keyword_recall_loras)} 个LORA, IDs: {list(keyword_recall_loras)[:10]}")
            else:
                logger.info(f"[LORA混合召回] 查询中未提取到关键词")

        # ========== 第三阶段：合并结果并计算混合得分 ==========
        all_lora_ids = set(vector_recall_loras.keys()) | keyword_recall_loras

        # 按名称去重（同一LORA的high/low版本只保留一个）
        name_to_best_lora = {}

        for lora_id in all_lora_ids:
            lora_name = lora_metadata.get(lora_id, {}).get("name", "")
            if not lora_name:
                continue

            # 获取向量相似度（如果有）
            if lora_id in vector_recall_loras:
                tag_sim = vector_recall_loras[lora_id]["max_tag_similarity"]
                tag_count = vector_recall_loras[lora_id]["count"]
            else:
                # 关键词召回但向量未召回：给一个较高的基础相似度
                # 因为关键词匹配说明内容高度相关
                tag_sim = 0.75
                tag_count = 0

            # 计算名称相似度
            name_sim = self.calculate_name_similarity(query, lora_name)

            # 计算关键词得分
            keyword_score = 0.0
            if keyword_boost > 0 and query_keywords and lora_search_keywords:
                search_keywords = lora_search_keywords.get(lora_id, "")
                if search_keywords:
                    # 只使用查询中提取到的关键词来计算得分
                    keyword_score = self._calculate_keyword_score(query, search_keywords, query_keywords)
                    logger.info(f"[LORA关键词] LORA {lora_id}: keyword_score={keyword_score:.3f}, query_keywords={query_keywords}, search_keywords前50字符='{search_keywords[:50]}'")
                else:
                    logger.info(f"[LORA关键词] LORA {lora_id}: 无search_keywords")
            else:
                logger.info(f"[LORA关键词] LORA {lora_id}: 跳过关键词计算 (keyword_boost={keyword_boost}, query_keywords={'有' if query_keywords else '无'}, lora_search_keywords={'有' if lora_search_keywords else '无'})")

            # 如果有关键词匹配，提升tag_sim（关键词匹配说明内容高度相关）
            if keyword_score > 0:
                # 将tag_sim提升到至少0.95
                tag_sim = max(tag_sim, 0.95)

            # 综合分数计算
            base_score = tag_sim * (1 - name_weight) + name_sim * name_weight

            if keyword_boost > 0 and feature_keywords and keyword_score > 0:
                # 只有当关键词匹配时才应用加权
                final_score = base_score * (1 - keyword_boost) + keyword_score * keyword_boost
            else:
                # 关键词不匹配时保持原始得分
                final_score = base_score

            logger.info(
                f"[LORA得分] LORA {lora_id} ({lora_name}): "
                f"tag_sim={tag_sim:.3f}, name_sim={name_sim:.3f}, "
                f"keyword_score={keyword_score:.3f}, base_score={base_score:.3f}, final={final_score:.3f}, "
                f"keyword_boost={keyword_boost}, name_weight={name_weight}"
            )

            # 按名称去重：保留相似度更高的版本
            if lora_name not in name_to_best_lora:
                name_to_best_lora[lora_name] = {
                    "lora_id": lora_id,
                    "similarity": final_score,
                    "tag_similarity": tag_sim,
                    "name_similarity": name_sim,
                    "keyword_score": keyword_score,
                    "tag_count": tag_count
                }
            else:
                # 如果已存在同名LORA，保留相似度更高的
                existing_sim = name_to_best_lora[lora_name]["similarity"]
                if final_score > existing_sim:
                    name_to_best_lora[lora_name] = {
                        "lora_id": lora_id,
                        "similarity": final_score,
                        "tag_similarity": tag_sim,
                        "name_similarity": name_sim,
                        "keyword_score": keyword_score,
                        "tag_count": tag_count
                    }

            logger.debug(
                f"LORA {lora_id} ({lora_name}): "
                f"tag_sim={tag_sim:.3f}, name_sim={name_sim:.3f}, "
                f"keyword_score={keyword_score:.3f}, final={final_score:.3f}"
            )

        # 转换为列表
        final_scores = [
            {
                "lora_id": data["lora_id"],
                "similarity": data["similarity"],
                "tag_similarity": data["tag_similarity"],
                "name_similarity": data["name_similarity"],
                "keyword_score": data["keyword_score"],
                "tag_count": data["tag_count"]
            }
            for lora_name, data in name_to_best_lora.items()
        ]

        # 过滤低于阈值的结果
        filtered_scores = [s for s in final_scores if s["similarity"] >= min_similarity]

        # 按相似度排序
        filtered_scores.sort(key=lambda x: x["similarity"], reverse=True)

        # 返回top_k结果
        return filtered_scores[:top_k]


# 全局实例
_embedding_service_v2: Optional[EmbeddingServiceV2] = None


def get_embedding_service_v2() -> EmbeddingServiceV2:
    """获取Embedding服务V2单例"""
    global _embedding_service_v2
    if _embedding_service_v2 is None:
        _embedding_service_v2 = EmbeddingServiceV2(device='cpu')
    return _embedding_service_v2
