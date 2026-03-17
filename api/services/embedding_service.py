"""
Embedding服务 - 使用BGE-Large-ZH模型
"""
import asyncio
import logging
import time
import json
import os
from typing import List, Optional
import numpy as np
from sentence_transformers import SentenceTransformer
from pymilvus import connections, Collection, utility, CollectionSchema, FieldSchema, DataType

logger = logging.getLogger(__name__)

# 加载关键词配置（包含近义词映射和boost_weight）
KEYWORD_SYNONYMS = {}
BOOST_WEIGHT = 0.35
FEATURE_KEYWORDS = []

try:
    config_path = os.path.join(os.path.dirname(__file__), '../../config/keywords.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

        # 提取配置参数
        if '_config' in config:
            BOOST_WEIGHT = config['_config'].get('boost_weight', 0.35)
            del config['_config']  # 移除配置项，只保留关键词

        KEYWORD_SYNONYMS = config

        # 构建特征关键词列表：主关键词 + 所有同义词 + 所有相关词
        feature_keywords_set = set()
        for main_keyword, keyword_config in config.items():
            feature_keywords_set.add(main_keyword)
            feature_keywords_set.update(keyword_config.get('synonyms', []))
            feature_keywords_set.update(keyword_config.get('related_words', []))

        FEATURE_KEYWORDS = sorted(list(feature_keywords_set))

        logger.info(f"已加载 {len(KEYWORD_SYNONYMS)} 个主关键词，生成 {len(FEATURE_KEYWORDS)} 个特征关键词，boost_weight={BOOST_WEIGHT}")
except Exception as e:
    logger.warning(f"无法加载关键词配置: {e}")
    KEYWORD_SYNONYMS = {}
    FEATURE_KEYWORDS = []
    BOOST_WEIGHT = 0.35


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

    async def index_resource(self, resource_id: int, prompt: str, search_keywords: Optional[str] = None, enabled: bool = True):
        """
        为资源建立索引

        Args:
            resource_id: 资源ID
            prompt: 资源的prompt
            search_keywords: 搜索关键词（可选）
            enabled: 是否启用（是否被收藏）
        """
        # 组合prompt和search_keywords用于embedding
        text_for_embedding = prompt
        if search_keywords:
            # 优化关键词格式：将逗号分隔转换为空格分隔，提升语义理解
            # "word1, word2, word3" -> "word1 word2 word3"
            cleaned_keywords = search_keywords.replace(',', ' ').replace('  ', ' ').strip()
            text_for_embedding = f"{prompt} {cleaned_keywords}"

        embedding = await self.embed(text_for_embedding)

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
        logger.info(f"已为资源 {resource_id} 建立索引 (enabled={enabled}, with_keywords={bool(search_keywords)})")

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

        # 优化关键词格式：将逗号分隔转换为空格分隔
        cleaned_prompts = []
        for prompt in example_prompts:
            if ',' in prompt and len(prompt.split(',')) > 3:
                # 如果是逗号分隔的关键词列表，转换为空格分隔
                cleaned = prompt.replace(',', ' ').replace('  ', ' ').strip()
                cleaned_prompts.append(cleaned)
            else:
                # 保持原样（可能已经是自然语言）
                cleaned_prompts.append(prompt)

        embeddings = await self.batch_embed(cleaned_prompts)

        data = []
        for prompt, embedding in zip(cleaned_prompts, embeddings):
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
        logger.info(f"已为LORA {lora_id} 建立 {len(cleaned_prompts)} 个索引 (enabled={enabled})")

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


    def _calculate_keyword_score(self, query: str, prompt: str, feature_keywords: List[str]) -> float:
        """
        计算关键词匹配得分（考虑位置、突出程度和近义词）

        Args:
            query: 查询文本
            prompt: 资源prompt或search_keywords
            feature_keywords: 特征关键词列表

        Returns:
            关键词匹配得分 (0-1)
        """
        query_lower = query.lower()
        prompt_lower = prompt.lower()

        total_score = 0.0
        total_keywords_in_query = 0

        for keyword in feature_keywords:
            # 检查查询中是否包含该关键词（完整短语匹配或词级别匹配）
            keyword_lower = keyword.lower()

            # 方法1: 完整短语匹配
            has_match = keyword_lower in query_lower

            # 方法2: 如果关键词是多词短语，检查所有词是否都在查询中（考虑词频）
            if not has_match and ' ' in keyword_lower:
                from collections import Counter
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
                    matched_words = sum(1 for kw in keyword_counter if query_counter[kw] > 0)
                    if len(keyword_words) >= 3 and matched_words >= len(keyword_words) * 0.6:
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
                                positions.append(-1)
                        valid_positions = [p for p in positions if p >= 0]
                        if (len(valid_positions) >= len(keyword_words) * 0.6 and
                            positions[0] >= 0 and positions[-1] >= 0):
                            max_gap = max(valid_positions[i+1] - valid_positions[i] for i in range(len(valid_positions)-1)) if len(valid_positions) > 1 else 0
                            if max_gap <= 2:
                                has_match = True

            if has_match:
                total_keywords_in_query += 1

                # 检查直接匹配
                matched = False
                if keyword in prompt_lower:
                    matched = True
                    match_keyword = keyword
                else:
                    # 检查近义词匹配
                    for main_keyword, config in KEYWORD_SYNONYMS.items():
                        all_variants = [main_keyword] + config.get('synonyms', []) + config.get('related_words', [])

                        # 检查查询关键词是否包含主关键词或其变体
                        keyword_matches_config = False
                        for variant in all_variants:
                            if variant.lower() in keyword.lower() or keyword.lower() in variant.lower():
                                keyword_matches_config = True
                                break

                        if keyword_matches_config:
                            # 在prompt中查找任何变体
                            for variant in all_variants:
                                if variant.lower() in prompt_lower:
                                    matched = True
                                    match_keyword = variant.lower()
                                    break
                            if matched:
                                break

                if matched:
                    # 基础匹配得分
                    # 如果关键词在配置表中（重要关键词），提高基础分
                    base_score = 1.0
                    is_important_keyword = False

                    # 检查是否是配置表中的重要关键词
                    for main_keyword in KEYWORD_SYNONYMS.keys():
                        if main_keyword.lower() in keyword.lower() or keyword.lower() in main_keyword.lower():
                            base_score = 1.5  # 重要关键词基础分提升50%
                            is_important_keyword = True
                            break

                    # 位置加权：关键词出现在前面得分更高
                    position = prompt_lower.find(match_keyword)
                    prompt_length = len(prompt_lower)
                    position_weight = 1.0 - (position / prompt_length) * 0.5  # 前面的词权重更高

                    # 突出程度：检查是否在强调位置（如括号、开头等）
                    prominence_weight = 1.0

                    # 检查是否在开头（前50个字符）
                    if position < 50:
                        prominence_weight += 0.3

                    # 检查是否在括号中强调，如 (doggy style:1.1)
                    if f"({match_keyword}" in prompt_lower or f"{match_keyword}:" in prompt_lower:
                        prominence_weight += 0.5

                    # 近义词匹配额外加分
                    if match_keyword != keyword:
                        prominence_weight += 0.4  # 近义词匹配额外加40%

                    # 检查关键词出现次数
                    count = prompt_lower.count(match_keyword)
                    count_weight = min(1.0 + (count - 1) * 0.2, 1.5)  # 多次出现加分，最多1.5倍

                    # 综合得分
                    keyword_score = base_score * position_weight * prominence_weight * count_weight
                    total_score += min(keyword_score, 2.0)  # 单个关键词最高2分

        if total_keywords_in_query == 0:
            return 0.0

        # 归一化到 0-1
        # 不除以 total_keywords_in_query，避免被查询中的多个关键词稀释
        # 只要有匹配就给高分
        return min(total_score / 2.0, 1.0)  # 除以2是因为最高分是2，归一化到0-1

    def _hybrid_score(self, semantic_sim: float, keyword_score: float, boost_weight: float) -> float:
        """
        计算混合得分

        Args:
            semantic_sim: 语义相似度 (0-1)
            keyword_score: 关键词匹配得分 (0-1)
            boost_weight: 关键词权重 (0-1)

        Returns:
            混合得分 (0-1)
        """
        return semantic_sim * (1 - boost_weight) + keyword_score * boost_weight

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

    async def search_similar_resources(
        self,
        query: str,
        top_k: int = 10,
        boost_weight: float = 0.0,
        feature_keywords: Optional[List[str]] = None,
        resource_search_keywords: Optional[dict] = None
    ) -> List[dict]:
        """
        搜索相似资源（混合召回：向量搜索 + 关键词召回）

        Args:
            query: 查询文本
            top_k: 返回top-k个结果
            boost_weight: 关键词权重 (0-1)，0表示纯语义搜索
            feature_keywords: 特征关键词列表，用于加权
            resource_search_keywords: 资源的search_keywords字典 {resource_id: "keywords"}

        Returns:
            相似资源列表
        """
        # 第一阶段：向量召回
        query_embedding = await self.embed(query)
        search_params = {"metric_type": "IP", "params": {"ef": 64}}
        search_limit = top_k * 2 if boost_weight > 0 and feature_keywords else top_k

        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=search_limit,
            expr='type == "resource"',
            output_fields=["resource_id", "prompt"]
        )

        # 收集向量召回的资源ID
        vector_recall_ids = set()
        vector_recall_data = {}
        for hit in results[0]:
            resource_id = hit.entity.get("resource_id")
            vector_recall_ids.add(resource_id)
            vector_recall_data[resource_id] = {
                "prompt": hit.entity.get("prompt"),
                "semantic_sim": float(hit.distance)
            }

        # 第二阶段：关键词召回（如果启用关键词加权）
        keyword_recall_ids = set()
        query_keywords = []  # 初始化为空列表
        if boost_weight > 0 and feature_keywords and resource_search_keywords:
            # 提取查询中的关键词
            query_keywords = self._extract_query_keywords(query, feature_keywords)

            if query_keywords:
                # 获取所有变体（包括近义词）
                keyword_variants = self._get_keyword_variants(query_keywords)
                logger.info(f"查询关键词: {query_keywords}, 变体: {keyword_variants[:10]}...")

                # 从resource_search_keywords中召回包含任何变体的资源
                for resource_id, keywords_text in resource_search_keywords.items():
                    if not keywords_text:
                        continue

                    keywords_lower = keywords_text.lower()
                    # 检查是否包含任何变体
                    for variant in keyword_variants:
                        if variant.lower() in keywords_lower:
                            keyword_recall_ids.add(resource_id)
                            break

                logger.info(f"关键词召回: {len(keyword_recall_ids)} 个资源")

        # 第三阶段：合并召回结果
        all_resource_ids = vector_recall_ids | keyword_recall_ids

        # 对所有召回的资源计算混合得分
        items = []
        for resource_id in all_resource_ids:
            # 获取语义相似度
            if resource_id in vector_recall_data:
                semantic_sim = vector_recall_data[resource_id]["semantic_sim"]
                prompt = vector_recall_data[resource_id]["prompt"]
            else:
                # 关键词召回但不在向量召回中，给一个基础语义分
                semantic_sim = 0.6  # 基础分（提高到0.6，因为关键词匹配说明内容高度相关）
                prompt = ""

            # 计算关键词得分
            keyword_score = 0.0
            if boost_weight > 0 and query_keywords:
                search_text = prompt
                if resource_search_keywords:
                    keywords = resource_search_keywords.get(resource_id, "")
                    if keywords:
                        import re
                        search_text = re.sub(r'\s+', ' ', keywords.replace(",", " "))

                # 只使用查询中提取到的关键词来计算得分
                keyword_score = self._calculate_keyword_score(query, search_text, query_keywords)

            # 计算混合得分
            if boost_weight > 0 and feature_keywords:
                final_score = self._hybrid_score(semantic_sim, keyword_score, boost_weight)
            else:
                final_score = semantic_sim

            items.append({
                "resource_id": resource_id,
                "prompt": prompt,
                "similarity": final_score,
                "semantic_similarity": semantic_sim,
                "keyword_score": keyword_score
            })

        # 按混合得分排序
        items.sort(key=lambda x: x['similarity'], reverse=True)

        # 返回top-k结果
        items = items[:top_k]

        logger.info(f"搜索完成: 向量召回{len(vector_recall_ids)}, 关键词召回{len(keyword_recall_ids)}, 返回{len(items)}个结果")
        return items

    async def search_similar_loras(
        self,
        query: str,
        mode: Optional[str] = None,
        top_k: int = 10,
        boost_weight: float = 0.0,
        feature_keywords: Optional[List[str]] = None,
        lora_search_keywords: Optional[dict] = None
    ) -> List[dict]:
        """
        搜索相似LORA（混合召回：向量搜索 + 关键词召回）

        Args:
            query: 查询文本
            mode: 模式筛选 (I2V, T2V, both, None表示不筛选)
            top_k: 返回top-k个结果
            boost_weight: 关键词权重 (0-1)，0表示纯语义搜索
            feature_keywords: 特征关键词列表，用于加权
            lora_search_keywords: LORA的search_keywords字典 {lora_id: "keywords"}

        Returns:
            相似LORA列表
        """
        # 第一阶段：向量召回
        query_embedding = await self.embed(query)
        search_params = {"metric_type": "IP", "params": {"ef": 64}}
        search_limit = top_k * 2 if boost_weight > 0 and feature_keywords else top_k * 2

        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=search_limit,
            expr='type == "lora"',
            output_fields=["lora_id", "prompt"]
        )

        # 收集向量召回的LORA ID及其语义分数
        vector_recall_loras = {}
        for hit in results[0]:
            lora_id = hit.entity.get("lora_id")
            score = float(hit.distance)
            prompt = hit.entity.get("prompt")

            if lora_id not in vector_recall_loras:
                vector_recall_loras[lora_id] = {
                    "max_score": score,
                    "count": 1,
                    "prompt": prompt
                }
            else:
                if score > vector_recall_loras[lora_id]["max_score"]:
                    vector_recall_loras[lora_id]["max_score"] = score
                    vector_recall_loras[lora_id]["prompt"] = prompt
                vector_recall_loras[lora_id]["count"] += 1

        # 第二阶段：关键词召回（如果启用关键词加权）
        keyword_recall_loras = set()
        if boost_weight > 0 and feature_keywords and lora_search_keywords:
            # 提取查询中的关键词
            query_keywords = self._extract_query_keywords(query, feature_keywords)

            if query_keywords:
                # 获取所有变体（包括近义词）
                keyword_variants = self._get_keyword_variants(query_keywords)
                logger.info(f"LORA查询关键词: {query_keywords}, 变体: {keyword_variants[:10]}...")

                # 从lora_search_keywords中召回包含任何变体的LORA
                for lora_id, keywords_text in lora_search_keywords.items():
                    if not keywords_text:
                        continue

                    keywords_lower = keywords_text.lower()
                    # 检查是否包含任何变体
                    for variant in keyword_variants:
                        if variant.lower() in keywords_lower:
                            keyword_recall_loras.add(lora_id)
                            break

                logger.info(f"LORA关键词召回: {len(keyword_recall_loras)} 个")

        # 第三阶段：合并召回结果
        all_lora_ids = set(vector_recall_loras.keys()) | keyword_recall_loras

        # 对所有召回的LORA计算混合得分
        items = []
        for lora_id in all_lora_ids:
            # 获取语义相似度
            if lora_id in vector_recall_loras:
                semantic_sim = vector_recall_loras[lora_id]["max_score"]
                prompt = vector_recall_loras[lora_id]["prompt"]
                count = vector_recall_loras[lora_id]["count"]
            else:
                # 关键词召回但不在向量召回中，给一个基础语义分
                semantic_sim = 0.6  # 基础分
                prompt = ""
                count = 1

            # 计算关键词得分
            keyword_score = 0.0
            if boost_weight > 0 and feature_keywords:
                search_text = prompt
                if lora_search_keywords:
                    keywords = lora_search_keywords.get(lora_id, "")
                    if keywords:
                        import re
                        search_text = re.sub(r'\s+', ' ', keywords.replace(",", " "))

                keyword_score = self._calculate_keyword_score(query, search_text, feature_keywords)

            # 计算混合得分
            if boost_weight > 0 and feature_keywords:
                final_score = self._hybrid_score(semantic_sim, keyword_score, boost_weight)
            else:
                final_score = semantic_sim

            items.append({
                "lora_id": lora_id,
                "similarity": final_score,
                "semantic_similarity": semantic_sim,
                "keyword_score": keyword_score,
                "count": count
            })

        # 按混合得分排序，相同时按匹配数量、LORA ID排序
        items.sort(key=lambda x: (x['similarity'], x['count'], -x['lora_id']), reverse=True)

        # 返回top-k结果
        items = items[:top_k]

        logger.info(f"LORA搜索完成: 向量召回{len(vector_recall_loras)}, 关键词召回{len(keyword_recall_loras)}, 返回{len(items)}个结果")
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

        search_params = {"metric_type": "IP", "params": {"ef": 64}}

        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k * 2,  # 多取一些，因为一个image LORA可能有多个tags
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

    async def clear_all(self):
        """清空所有索引"""
        try:
            # Delete all entities
            self.collection.delete(expr="id >= 0")
            self.collection.flush()
            logger.info("已清空所有索引")
        except Exception as e:
            logger.error(f"Failed to clear all embeddings: {e}")
            raise

    async def get_stats(self) -> dict:
        """获取索引统计信息"""
        await asyncio.to_thread(self.collection.load)
        total = self.collection.num_entities

        # 简化统计，避免慢查询
        # 返回总数，不做详细分类（分类查询太慢）

        return {
            "total_count": total,
            "resource_count": 0,  # 暂不统计，避免慢查询
            "lora_count": 0,      # 暂不统计，避免慢查询
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
