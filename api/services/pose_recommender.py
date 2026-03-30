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


MIN_RECOMMEND_SCORE = 0.5  # 低于此分数的推荐不返回


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

        # 在独立线程中运行，避免与 FastAPI 的事件循环冲突
        import concurrent.futures

        def run_in_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                new_loop.run_until_complete(build())
            finally:
                new_loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(run_in_thread).result()

    async def recommend(self, prompt: str, selected_poses: List[str] = None, top_k: int = 5, use_llm: bool = True, use_embedding: bool = True, min_score: float = None) -> List[PoseRecommendation]:
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
        original_prompt = prompt.lower().replace('-', ' ')
        expanded_prompt = expand_query(prompt)

        # 阶段2: 选择匹配策略
        if use_embedding and self.use_embedding and self.embedding_service:
            # 使用embedding匹配
            candidates = await self._embedding_match(expanded_prompt, top_k * 2)
        else:
            # 降级到关键词匹配
            candidates = await self._keyword_match(original_prompt, expanded_prompt, top_k * 2)

        if not candidates:
            return []

        # 阶段3: LLM重排序（可选）
        # 如果最高分候选 score >= 0.85，直接信任匹配结果，不调用LLM
        top_score = candidates[0].score if candidates else 0
        if use_llm and len(candidates) > 1 and top_score < 0.85:
            candidates = await self._llm_rerank(prompt, candidates)

        threshold = min_score if min_score is not None else MIN_RECOMMEND_SCORE
        filtered = [c for c in candidates if c.score >= threshold]
        return filtered[:top_k]
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

        # 传给LLM前先按score降序排列，保证高分候选在前
        sorted_candidates = sorted(candidates, key=lambda x: x.score, reverse=True)
        poses_desc = "\n".join([
            f"{i+1}. {c.name_en} ({c.name_cn}) [score:{c.score:.2f}] - {self.poses_data.get(c.pose_key, {}).get('description', c.name_en)}"
            for i, c in enumerate(sorted_candidates)
        ])

        system_prompt = """/no_think You are a sex position classifier. Your ONLY job is to rank candidate positions by relevance to the user query.
Output ONLY a JSON array of integers like [1,2,3]. No explanation."""

        user_prompt = f"""Query: "{prompt}"

Candidates (sorted by keyword score, higher = better pre-match):
{poses_desc}

Rules:
- Focus on the PRIMARY sexual act in the query.
- Candidate with score >= 0.8 is almost certainly correct, keep it at rank 1.
- Re-rank ALL candidates by how well they match the query.
- Output ONLY a JSON array of integers representing your ranked order. Do NOT output [1,2,3] by default — actually reason about the best match."""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(LLM_API_URL, json={
                    "model": "Qwen3-14B-v2-Abliterated",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 100
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
                            reranked = [sorted_candidates[idx-1] for idx in ranking if 1 <= idx <= len(sorted_candidates)]
                            return reranked if reranked else sorted_candidates
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
