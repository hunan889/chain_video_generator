"""
智能推荐API路由
"""
import logging
import asyncio
import time
import json
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from api.middleware.auth import verify_api_key
from api.services.embedding_service import get_embedding_service, FEATURE_KEYWORDS, BOOST_WEIGHT
from api.services.embedding_service_v2 import get_embedding_service_v2
import pymysql

logger = logging.getLogger(__name__)
router = APIRouter()


class PerformanceTimer:
    """性能计时器 - 用于追踪每个步骤的耗时"""

    def __init__(self):
        self.start_time = time.time()
        self.checkpoints = {}
        self.last_checkpoint = self.start_time

    def checkpoint(self, name: str):
        """记录一个检查点"""
        now = time.time()
        elapsed = now - self.last_checkpoint
        total_elapsed = now - self.start_time
        self.checkpoints[name] = {
            "elapsed": round(elapsed * 1000, 2),  # 转换为毫秒
            "total": round(total_elapsed * 1000, 2)
        }
        self.last_checkpoint = now
        logger.info(f"⏱️  [{name}] +{elapsed*1000:.0f}ms (total: {total_elapsed*1000:.0f}ms)")

    def get_summary(self) -> Dict[str, Any]:
        """获取性能摘要"""
        total_time = time.time() - self.start_time
        return {
            "total_ms": round(total_time * 1000, 2),
            "checkpoints": self.checkpoints
        }

# 数据库配置
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}


class RecommendRequest(BaseModel):
    prompt: str
    mode: Optional[str] = None  # I2V, T2V, both
    include_images: bool = True
    include_loras: bool = True
    top_k_images: int = 5
    top_k_loras: int = 5
    only_enabled: bool = True  # 默认只推荐已启用的LORA
    min_similarity: float = 0.6  # 最低相似度阈值 (0-1)，低于此值的结果会被过滤
    name_weight: float = 0.2  # 名称相似度权重 (0-1)，用于LORA搜索
    keyword_boost: Optional[float] = None  # 关键词加权 (0-1)，None表示使用默认配置


class RecommendedImage(BaseModel):
    resource_id: int
    prompt: str
    url: str
    similarity: float
    resource_type: Optional[str] = None
    search_keywords: Optional[str] = None
    semantic_similarity: Optional[float] = None  # 纯语义相似度
    keyword_score: Optional[float] = None  # 关键词匹配得分


class RecommendedVideoLora(BaseModel):
    lora_id: int
    name: str
    description: Optional[str]
    trigger_words: list[str]
    tags: list[str]
    mode: str  # I2V, T2V, both
    noise_stage: str  # high, low, single
    category: Optional[str]
    similarity: float
    preview_url: Optional[str]
    source: str  # "semantic" or "llm"
    search_keywords: Optional[str] = None
    trigger_prompt: Optional[str] = None


class RecommendedImageLora(BaseModel):
    lora_id: str
    name: str
    description: Optional[str]
    category: Optional[str]
    similarity: float
    source: str  # "semantic" or "llm"
    search_keywords: Optional[str] = None
    trigger_prompt: Optional[str] = None


class RecommendResponse(BaseModel):
    images: list[RecommendedImage]
    video_loras: list[RecommendedVideoLora]
    image_loras: list[RecommendedImageLora]
    optimized_prompt: Optional[str] = None
    performance: Optional[Dict[str, Any]] = None  # 性能追踪数据


@router.post("/recommend", response_model=RecommendResponse)
async def smart_recommend(req: RecommendRequest, _=Depends(verify_api_key)):
    """智能推荐：综合语义搜索和LLM推荐"""
    timer = PerformanceTimer()

    try:
        if not req.prompt.strip():
            raise HTTPException(400, "Prompt cannot be empty")

        timer.checkpoint("1_validate_input")

        embedding_service = get_embedding_service()
        embedding_service_v2 = get_embedding_service_v2()

        timer.checkpoint("2_get_services")

        # 准备元数据
        lora_metadata = None
        lora_search_keywords = None
        resource_search_keywords = None

        # 如果需要关键词加权，获取search_keywords
        boost_weight = req.keyword_boost if req.keyword_boost is not None else BOOST_WEIGHT
        feature_keywords = FEATURE_KEYWORDS if boost_weight > 0 else None

        if req.include_loras or (req.include_images and boost_weight > 0):
            conn = pymysql.connect(**DB_CONFIG)
            timer.checkpoint("3_db_connect_meta")

            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 获取LORA元数据
            if req.include_loras:
                cursor.execute("SELECT id, name, search_keywords FROM lora_metadata")
                rows = cursor.fetchall()
                lora_metadata = {row['id']: {"name": row['name']} for row in rows}
                lora_search_keywords = {row['id']: row['search_keywords'] or "" for row in rows}

            # 获取资源的search_keywords（用于关键词加权）
            if req.include_images and boost_weight > 0:
                cursor.execute("SELECT id, search_keywords FROM resources")
                rows = cursor.fetchall()
                resource_search_keywords = {row['id']: row['search_keywords'] or "" for row in rows}

            cursor.close()
            conn.close()

            timer.checkpoint("4_fetch_metadata")

        # 并行执行语义搜索
        tasks = []

        if req.include_images:
            tasks.append(embedding_service.search_similar_resources(
                query=req.prompt,
                top_k=req.top_k_images,
                boost_weight=boost_weight,
                feature_keywords=feature_keywords,
                resource_search_keywords=resource_search_keywords
            ))
        else:
            tasks.append(asyncio.sleep(0))  # Placeholder

        if req.include_loras:
            tasks.append(embedding_service_v2.search_similar_loras_v2(
                query=req.prompt,
                lora_metadata=lora_metadata,
                mode=req.mode,
                top_k=req.top_k_loras,
                name_weight=req.name_weight,
                min_similarity=req.min_similarity,
                keyword_boost=boost_weight,
                feature_keywords=feature_keywords,
                lora_search_keywords=lora_search_keywords
            ))
        else:
            tasks.append(asyncio.sleep(0))  # Placeholder

        timer.checkpoint("5_prepare_search_tasks")

        results = await asyncio.gather(*tasks)

        timer.checkpoint("6_vector_search_complete")

        image_results = results[0] if req.include_images else []
        lora_results = results[1] if req.include_loras else []

        # 处理图片结果
        images = []
        if image_results and len(image_results) > 0:
            resource_ids = []
            similarity_map = {}
            semantic_map = {}
            keyword_map = {}
            for item in image_results:
                resource_id = item.get('resource_id')
                similarity = item.get('similarity', 0.0)
                semantic_similarity = item.get('semantic_similarity', similarity)
                keyword_score = item.get('keyword_score', 0.0)
                # 过滤低于阈值的结果
                if similarity >= req.min_similarity:
                    resource_ids.append(resource_id)
                    similarity_map[resource_id] = similarity
                    semantic_map[resource_id] = semantic_similarity
                    keyword_map[resource_id] = keyword_score

            timer.checkpoint("7_filter_image_results")

            if resource_ids:
                conn = pymysql.connect(**DB_CONFIG)
                timer.checkpoint("8_db_connect_images")

                cursor = conn.cursor(pymysql.cursors.DictCursor)
                placeholders = ','.join(['%s'] * len(resource_ids))
                cursor.execute(f"""
                    SELECT id, prompt, url, resource_type, search_keywords
                    FROM resources
                    WHERE id IN ({placeholders})
                """, resource_ids)
                resources = cursor.fetchall()
                cursor.close()
                conn.close()

                timer.checkpoint("9_fetch_image_details")

                for resource in resources:
                    images.append(RecommendedImage(
                        resource_id=resource['id'],
                        prompt=resource['prompt'] or '',
                        url=resource['url'],
                        similarity=similarity_map.get(resource['id'], 0.0),
                        resource_type=resource.get('resource_type'),
                        search_keywords=resource.get('search_keywords'),
                        semantic_similarity=semantic_map.get(resource['id']),
                        keyword_score=keyword_map.get(resource['id'])
                    ))

                images.sort(key=lambda x: x.similarity, reverse=True)
                timer.checkpoint("10_build_image_response")

        # 处理LORA结果（分离视频LORA和图片LORA）
        video_loras = []
        image_loras = []

        if lora_results and len(lora_results) > 0:
            lora_ids = []
            similarity_map = {}
            for item in lora_results:
                lora_id = item.get('lora_id')
                similarity = item.get('similarity', 0.0)
                # 过滤低于阈值的结果
                if similarity >= req.min_similarity:
                    lora_ids.append(lora_id)
                    similarity_map[lora_id] = similarity

            timer.checkpoint("11_filter_lora_results")

            if lora_ids:
                conn = pymysql.connect(**DB_CONFIG)
                timer.checkpoint("12_db_connect_loras")

                cursor = conn.cursor(pymysql.cursors.DictCursor)

                # 查询视频LORA
                placeholders = ','.join(['%s'] * len(lora_ids))
                where_clause = f"id IN ({placeholders})"
                if req.only_enabled:
                    where_clause += " AND (enabled = 1 OR enabled = TRUE)"

                cursor.execute(f"""
                    SELECT id, name, description, trigger_words, tags, mode, noise_stage, category, preview_url, search_keywords, trigger_prompt
                    FROM lora_metadata
                    WHERE {where_clause}
                """, lora_ids)
                video_lora_data = cursor.fetchall()

                timer.checkpoint("13_fetch_lora_details")

                import json
                for lora in video_lora_data:
                    trigger_words = lora.get('trigger_words')
                    if isinstance(trigger_words, str):
                        try:
                            trigger_words = json.loads(trigger_words)
                        except:
                            trigger_words = []
                    if not trigger_words:
                        trigger_words = []

                    tags = lora.get('tags')
                    if isinstance(tags, str):
                        try:
                            tags = json.loads(tags)
                        except:
                            tags = []
                    if not tags:
                        tags = []

                    video_loras.append(RecommendedVideoLora(
                        lora_id=lora['id'],
                        name=lora['name'],
                        description=lora.get('description'),
                        trigger_words=trigger_words,
                        tags=tags,
                        mode=lora['mode'],
                        noise_stage=lora['noise_stage'],
                        category=lora.get('category'),
                        similarity=similarity_map.get(lora['id'], 0.0),
                        preview_url=lora.get('preview_url'),
                        source='semantic',
                        search_keywords=lora.get('search_keywords'),
                        trigger_prompt=lora.get('trigger_prompt')
                    ))

                video_loras.sort(key=lambda x: x.similarity, reverse=True)

                # TODO: 查询图片LORA（当向量数据库中有 type="image_lora" 时）
                # 目前图片LORA表为空，暂不实现
                # 未来需要：
                # 1. 在向量数据库中为图片LORA建立索引（type="image_lora"）
                # 2. 修改 embedding_service 支持搜索图片LORA
                # 3. 在这里查询 image_lora_metadata 表并构建 RecommendedImageLora

                cursor.close()
                conn.close()

                timer.checkpoint("14_build_lora_response")

        timer.checkpoint("15_complete")

        performance_summary = timer.get_summary()
        logger.info(f"🎯 搜索完成 - 总耗时: {performance_summary['total_ms']}ms")

        return RecommendResponse(
            images=images,
            video_loras=video_loras,
            image_loras=image_loras,
            optimized_prompt=None,
            performance=performance_summary
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate recommendations: {e}")
        raise HTTPException(500, f"推荐失败: {str(e)}")
