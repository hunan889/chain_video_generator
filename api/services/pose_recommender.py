"""
姿势推荐服务 - 三阶段推荐：同义词扩展 + Embedding匹配 + LLM重排序
"""
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import aiohttp
import json
import numpy as np
from .pose_synonyms import expand_query, get_synonyms
from .embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "wan22.db"
LLM_API_URL = "http://localhost:20001/v1/chat/completions"


@dataclass
class PoseRecommendation:
    """推荐结果"""
    pose_key: str
    name_cn: str
    name_en: str
    score: float
    match_reason: str
    category: str


class PoseRecommender:
    """姿势推荐引擎 - 三阶段推荐：同义词扩展 + Embedding匹配 + LLM重排序"""

    def __init__(self, db_path: str = None, use_embedding: bool = True):
        self.db_path = db_path or str(DB_PATH)
        self.poses_data = {}
        self.keyword_index = {}
        self.use_embedding = use_embedding
        self.embedding_service = None
        self.pose_embeddings = {}  # {pose_key: embedding_vector}

    def initialize(self):
        """初始化数据"""
        logger.info("Initializing pose recommender...")
        self._load_poses_data()
        self._build_keyword_index()

        # 初始化embedding服务
        if self.use_embedding:
            try:
                self.embedding_service = EmbeddingService(device='cpu')
                self._build_pose_embeddings()
                logger.info("Embedding service initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize embedding service: {e}, falling back to keyword matching")
                self.use_embedding = False

        logger.info(f"Initialized with {len(self.poses_data)} poses")

    def _load_poses_data(self):
        """从数据库加载姿势数据"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, pose_key, name_cn, name_en, category, description
            FROM poses
            WHERE enabled = 1
        """)

        for row in cursor.fetchall():
            search_keywords = self._build_search_keywords(row)
            self.poses_data[row['pose_key']] = {
                'id': row['id'],
                'pose_key': row['pose_key'],
                'name_cn': row['name_cn'],
                'name_en': row['name_en'],
                'category': row['category'],
                'description': row['description'] or '',
                'search_keywords': search_keywords,
                'keywords_set': set(search_keywords.lower().split())
            }

        cursor.close()
        conn.close()

    def _build_search_keywords(self, pose_row) -> str:
        """构建搜索关键词"""
        parts = [
            pose_row['name_en'] or '',
            pose_row['pose_key'].replace('_', ' '),
            pose_row['description'] or ''
        ]
        # 添加同义词
        synonyms = get_synonyms(pose_row['pose_key'])
        parts.extend(synonyms)

        return ' '.join(filter(None, parts)).lower()

    def _build_keyword_index(self):
        """构建关键词倒排索引"""
        for pose_key, pose_data in self.poses_data.items():
            for keyword in pose_data['keywords_set']:
                if keyword not in self.keyword_index:
                    self.keyword_index[keyword] = []
                self.keyword_index[keyword].append(pose_key)

    def _build_pose_embeddings(self):
        """为所有姿势构建embedding向量"""
        import asyncio

        async def build():
            texts = []
            pose_keys = []

            for pose_key, pose_data in self.poses_data.items():
                # 使用搜索关键词作为embedding文本
                texts.append(pose_data['search_keywords'])
                pose_keys.append(pose_key)

            # 批量生成embeddings
            embeddings = await self.embedding_service.batch_embed(texts)

            for pose_key, embedding in zip(pose_keys, embeddings):
                self.pose_embeddings[pose_key] = np.array(embedding)

            logger.info(f"Built embeddings for {len(self.pose_embeddings)} poses")

        # 在同步上下文中运行异步函数
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(build())

    async def recommend(self, prompt: str, selected_poses: List[str] = None, top_k: int = 5, use_llm: bool = True, use_embedding: bool = True) -> List[PoseRecommendation]:
        """
        推荐姿势 - 三阶段流程

        Args:
            prompt: 用户查询
            selected_poses: 已选姿势列表
            top_k: 返回数量
            use_llm: 是否使用LLM重排序
            use_embedding: 是否使用embedding匹配

        Returns:
            推荐结果列表
        """
        # 阶段1: 同义词扩展
        original_prompt = prompt.lower()
        expanded_prompt = expand_query(prompt)

        # 阶段2: 选择匹配策略
        if use_embedding and self.use_embedding and self.embedding_service:
            # 使用embedding匹配
            candidates = await self._embedding_match(expanded_prompt, top_k * 2)
        else:
            # 降级到关键词匹配
            candidates = await self._keyword_match(original_prompt, expanded_prompt, top_k * 2)

        # 如果没有候选，使用兜底策略
        if not candidates:
            logger.info("No candidates found, using fallback poses")
            fallback_poses = ['missionary', 'doggy', 'cowgirl', 'reverse_cowgirl', 'blowjob']
            candidates = []
            for pose_key in fallback_poses:
                if pose_key in self.poses_data:
                    pose_data = self.poses_data[pose_key]
                    candidates.append(PoseRecommendation(
                        pose_key=pose_key,
                        name_cn=pose_data['name_cn'],
                        name_en=pose_data['name_en'],
                        score=0.1,
                        match_reason=f"兜底推荐 {pose_data['name_cn']}",
                        category=pose_data['category'] or 'uncategorized'
                    ))

        # 阶段3: LLM重排序（可选）
        if use_llm and len(candidates) > 1:
            candidates = await self._llm_rerank(prompt, candidates)

        return candidates[:top_k]
    async def _embedding_match(self, query: str, candidate_count: int) -> List[PoseRecommendation]:
        """使用embedding进行语义匹配"""
        try:
            # 生成查询的embedding
            query_embedding = await self.embedding_service.embed(query)
            query_vec = np.array(query_embedding)

            # 计算与所有姿势的相似度
            similarities = []
            for pose_key, pose_embedding in self.pose_embeddings.items():
                # 计算余弦相似度（向量已归一化，直接点积）
                similarity = float(np.dot(query_vec, pose_embedding))
                similarities.append((pose_key, similarity))

            # 按相似度排序
            similarities.sort(key=lambda x: x[1], reverse=True)

            # 构建候选结果
            results = []
            for pose_key, score in similarities[:candidate_count]:
                if score < 0.3:  # 过滤低分结果
                    continue

                pose_data = self.poses_data[pose_key]
                results.append(PoseRecommendation(
                    pose_key=pose_key,
                    name_cn=pose_data['name_cn'],
                    name_en=pose_data['name_en'],
                    score=float(score),
                    match_reason=self._get_match_reason(pose_data, score),
                    category=pose_data['category'] or 'uncategorized'
                ))

            logger.info(f"Embedding match found {len(results)} candidates")
            return results

        except Exception as e:
            logger.error(f"Embedding match failed: {e}, falling back to keyword match")
            return await self._keyword_match(query, query, candidate_count)

    async def _keyword_match(self, original_prompt: str, expanded_prompt: str, candidate_count: int) -> List[PoseRecommendation]:
        """使用关键词进行匹配（降级方案）"""
        prompt_lower = expanded_prompt.lower()
        prompt_tokens = set(prompt_lower.split())

        # 计算文本匹配分数
        text_scores = {}
        for pose_key, pose_data in self.poses_data.items():
            # 方法1: 关键词重叠
            overlap = len(prompt_tokens & pose_data['keywords_set'])
            overlap_score = overlap / max(len(prompt_tokens), 1)

            # 方法2: 短语匹配（在原始查询中检查）
            phrase_score = 0
            if pose_data['name_en'].lower() in original_prompt:
                phrase_score = 1.0
            elif pose_data['pose_key'].replace('_', ' ') in original_prompt:
                phrase_score = 0.9
            else:
                # 检查同义词是否在原始查询中
                synonyms = get_synonyms(pose_key)
                matched_synonym_len = 0
                for syn in synonyms:
                    if syn.lower() in original_prompt:
                        # 多词短语匹配得分更高
                        syn_word_count = len(syn.split())
                        if syn_word_count > matched_synonym_len:
                            matched_synonym_len = syn_word_count
                            phrase_score = 0.9 + (syn_word_count * 0.02)
                            if phrase_score > 1.0:
                                phrase_score = 1.0

            # 方法3: 模糊匹配
            fuzzy_score = SequenceMatcher(None, original_prompt, pose_data['search_keywords']).ratio()

            # 综合分数
            text_scores[pose_key] = max(phrase_score, overlap_score * 0.7, fuzzy_score * 0.5)

        # 排序返回候选集
        sorted_poses = sorted(text_scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for pose_key, score in sorted_poses[:candidate_count]:
            if score < 0.05:  # 过滤低分结果
                continue

            pose_data = self.poses_data[pose_key]
            results.append(PoseRecommendation(
                pose_key=pose_key,
                name_cn=pose_data['name_cn'],
                name_en=pose_data['name_en'],
                score=float(score),
                match_reason=self._get_match_reason(pose_data, score),
                category=pose_data['category'] or 'uncategorized'
            ))

        logger.info(f"Keyword match found {len(results)} candidates")
        return results

    def _get_relation_scores(self, selected_poses: List[str]) -> Dict[str, float]:
        """基于已选姿势计算关联分数（已废弃，保留接口兼容性）"""
        return {}

    def _fuse_scores(self, text_scores: Dict[str, float], relation_scores: Dict[str, float]) -> Dict[str, float]:
        """融合分数（已废弃，保留接口兼容性）"""
        return text_scores

    def _get_match_reason(self, pose_data: Dict, score: float) -> str:
        """生成匹配原因"""
        if score > 0.7:
            return f"高度匹配 {pose_data['name_cn']}"
        elif score > 0.4:
            return f"匹配 {pose_data['name_cn']}"
        else:
            return f"相关 {pose_data['name_cn']}"

    async def _llm_rerank(self, prompt: str, candidates: List[PoseRecommendation]) -> List[PoseRecommendation]:
        """使用 LLM 对候选姿势重新排序"""
        if len(candidates) <= 1:
            return candidates

        poses_desc = "\n".join([f"{i+1}. {c.name_en} ({c.name_cn})" for i, c in enumerate(candidates)])

        user_prompt = f"""Query: "{prompt}"

Rank by relevance:
{poses_desc}

Output format: [2,1,3]
Your answer:"""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(LLM_API_URL, json={
                    "model": "Qwen3-14B-v2-Abliterated",
                    "messages": [
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 200
                }, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data['choices'][0]['message']['content'].strip()
                        logger.info(f"LLM response: {content}")
                        # 提取 JSON 数组
                        import re
                        match = re.search(r'\[[\d,\s]+\]', content)
                        if match:
                            ranking = json.loads(match.group())
                            logger.info(f"Extracted ranking: {ranking}")
                            reranked = [candidates[idx-1] for idx in ranking if 1 <= idx <= len(candidates)]
                            return reranked if reranked else candidates
                        else:
                            logger.warning(f"No JSON array found in LLM response")
        except Exception as e:
            logger.warning(f"LLM rerank failed: {e}")

        return candidates


# 单例
_recommender_instance = None

def get_pose_recommender() -> PoseRecommender:
    """获取姿势推荐器单例"""
    global _recommender_instance
    if _recommender_instance is None:
        _recommender_instance = PoseRecommender()
        _recommender_instance.initialize()
    return _recommender_instance
