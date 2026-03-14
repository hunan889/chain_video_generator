"""
智能推荐API路由
"""
import logging
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from api.middleware.auth import verify_api_key
from api.services.embedding_service import get_embedding_service
import pymysql

logger = logging.getLogger(__name__)
router = APIRouter()

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


class RecommendedImage(BaseModel):
    resource_id: int
    prompt: str
    url: str
    similarity: float


class RecommendedVideoLora(BaseModel):
    lora_id: int
    name: str
    description: Optional[str]
    trigger_words: list[str]
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


@router.post("/recommend", response_model=RecommendResponse)
async def smart_recommend(req: RecommendRequest, _=Depends(verify_api_key)):
    """智能推荐：综合语义搜索和LLM推荐"""
    try:
        if not req.prompt.strip():
            raise HTTPException(400, "Prompt cannot be empty")

        embedding_service = get_embedding_service()

        # 并行执行语义搜索
        tasks = []

        if req.include_images:
            tasks.append(embedding_service.search_similar_resources(
                query=req.prompt,
                top_k=req.top_k_images
            ))
        else:
            tasks.append(asyncio.sleep(0))  # Placeholder

        if req.include_loras:
            tasks.append(embedding_service.search_similar_loras(
                query=req.prompt,
                mode=req.mode,
                top_k=req.top_k_loras
            ))
        else:
            tasks.append(asyncio.sleep(0))  # Placeholder

        results = await asyncio.gather(*tasks)

        image_results = results[0] if req.include_images else []
        lora_results = results[1] if req.include_loras else []

        # 处理图片结果
        images = []
        if image_results and len(image_results) > 0:
            resource_ids = []
            similarity_map = {}
            for item in image_results:
                resource_id = item.get('resource_id')
                similarity = item.get('similarity', 0.0)
                # 过滤低于阈值的结果
                if similarity >= req.min_similarity:
                    resource_ids.append(resource_id)
                    similarity_map[resource_id] = similarity

            if resource_ids:
                conn = pymysql.connect(**DB_CONFIG)
                cursor = conn.cursor(pymysql.cursors.DictCursor)
                placeholders = ','.join(['%s'] * len(resource_ids))
                cursor.execute(f"""
                    SELECT id, prompt, url
                    FROM resources
                    WHERE id IN ({placeholders})
                """, resource_ids)
                resources = cursor.fetchall()
                cursor.close()
                conn.close()

                for resource in resources:
                    images.append(RecommendedImage(
                        resource_id=resource['id'],
                        prompt=resource['prompt'] or '',
                        url=resource['url'],
                        similarity=similarity_map.get(resource['id'], 0.0)
                    ))

                images.sort(key=lambda x: x.similarity, reverse=True)

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

            if lora_ids:
                conn = pymysql.connect(**DB_CONFIG)
                cursor = conn.cursor(pymysql.cursors.DictCursor)

                # 查询视频LORA
                placeholders = ','.join(['%s'] * len(lora_ids))
                where_clause = f"id IN ({placeholders})"
                if req.only_enabled:
                    where_clause += " AND (enabled = 1 OR enabled = TRUE)"

                cursor.execute(f"""
                    SELECT id, name, description, trigger_words, mode, noise_stage, category, preview_url, search_keywords, trigger_prompt
                    FROM lora_metadata
                    WHERE {where_clause}
                """, lora_ids)
                video_lora_data = cursor.fetchall()

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

                    video_loras.append(RecommendedVideoLora(
                        lora_id=lora['id'],
                        name=lora['name'],
                        description=lora.get('description'),
                        trigger_words=trigger_words,
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

        return RecommendResponse(
            images=images,
            video_loras=video_loras,
            image_loras=image_loras,
            optimized_prompt=None  # TODO: 可以集成LLM优化prompt
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate recommendations: {e}")
        raise HTTPException(500, f"推荐失败: {str(e)}")
