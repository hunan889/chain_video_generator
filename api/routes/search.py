"""
语义搜索API路由
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from api.middleware.auth import verify_api_key
from api.services.embedding_service import get_embedding_service
from api.services.embedding_service_v2 import get_embedding_service_v2
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


class SearchResourcesRequest(BaseModel):
    query: str
    top_k: int = 10


class SearchLorasRequest(BaseModel):
    query: str
    mode: Optional[str] = None  # I2V, T2V, both
    top_k: int = 10
    only_enabled: bool = True  # 默认只搜索已启用的LORA
    name_weight: float = 0.2  # 名称相似度权重 (0-1)
    min_similarity: float = 0.6  # 最低相似度阈值 (0-1)


class ResourceSearchResult(BaseModel):
    resource_id: int
    prompt: str
    url: str
    similarity: float
    resource_type: Optional[str] = None
    search_keywords: Optional[str] = None


class LoraSearchResult(BaseModel):
    lora_id: int
    name: str
    description: Optional[str]
    trigger_words: list[str]
    tags: list[str]
    mode: str
    noise_stage: str
    category: Optional[str]
    similarity: float
    preview_url: Optional[str]
    search_keywords: Optional[str] = None
    trigger_prompt: Optional[str] = None


@router.post("/search/resources", response_model=list[ResourceSearchResult])
async def search_similar_resources(req: SearchResourcesRequest, _=Depends(verify_api_key)):
    """搜索语义相似的资源"""
    import time
    start_time = time.time()

    try:
        if not req.query.strip():
            raise HTTPException(400, "Query cannot be empty")

        # 语义搜索
        t1 = time.time()
        embedding_service = get_embedding_service()
        results = await embedding_service.search_similar_resources(
            query=req.query,
            top_k=req.top_k
        )
        t2 = time.time()
        logger.info(f"[Performance] Vector search took {(t2-t1)*1000:.2f}ms")

        if not results or len(results) == 0:
            return []

        # 提取resource_ids
        resource_ids = []
        similarity_map = {}
        for item in results:
            resource_id = item.get('resource_id')
            similarity = item.get('similarity', 0.0)
            resource_ids.append(resource_id)
            similarity_map[resource_id] = similarity

        if not resource_ids:
            return []

        # 从数据库获取完整资源信息
        t3 = time.time()
        conn = pymysql.connect(**DB_CONFIG)
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
        t4 = time.time()
        logger.info(f"[Performance] Database query took {(t4-t3)*1000:.2f}ms")

        # 构建响应
        response = []
        for resource in resources:
            response.append(ResourceSearchResult(
                resource_id=resource['id'],
                prompt=resource['prompt'] or '',
                url=resource['url'],
                similarity=similarity_map.get(resource['id'], 0.0),
                resource_type=resource.get('resource_type'),
                search_keywords=resource.get('search_keywords')
            ))

        # 按相似度排序
        response.sort(key=lambda x: x.similarity, reverse=True)

        total_time = time.time() - start_time
        logger.info(f"[Performance] Total request took {total_time*1000:.2f}ms")

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to search resources: {e}")
        raise HTTPException(500, f"搜索失败: {str(e)}")


@router.post("/search/loras", response_model=list[LoraSearchResult])
async def search_similar_loras(req: SearchLorasRequest, _=Depends(verify_api_key)):
    """搜索语义相似的LORA"""
    try:
        if not req.query.strip():
            raise HTTPException(400, "Query cannot be empty")

        # 使用V2服务（支持名称加权）
        embedding_service = get_embedding_service_v2()

        # 先获取LORA元数据（用于名称相似度计算）
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute("SELECT id, name FROM lora_metadata")
        lora_metadata = {row['id']: {"name": row['name']} for row in cursor.fetchall()}
        cursor.close()
        conn.close()

        # 语义搜索（向量中只有 enabled 的 LORA）
        results = await embedding_service.search_similar_loras_v2(
            query=req.query,
            lora_metadata=lora_metadata,
            mode=req.mode,
            top_k=req.top_k,
            name_weight=req.name_weight,
            min_similarity=req.min_similarity
        )

        if not results or len(results) == 0:
            return []

        # 提取lora_ids
        lora_ids = []
        similarity_map = {}
        for item in results:
            lora_id = item.get('lora_id')
            similarity = item.get('similarity', 0.0)
            lora_ids.append(lora_id)
            similarity_map[lora_id] = similarity

        if not lora_ids:
            return []

        # 从数据库获取完整LORA信息
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        placeholders = ','.join(['%s'] * len(lora_ids))

        cursor.execute(f"""
            SELECT id, name, description, trigger_words, tags, mode, noise_stage, category, preview_url, search_keywords, trigger_prompt
            FROM lora_metadata
            WHERE id IN ({placeholders})
        """, lora_ids)

        loras = cursor.fetchall()
        cursor.close()
        conn.close()

        # 构建响应
        import json
        response = []
        for lora in loras:
            # 解析JSON字段
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

            response.append(LoraSearchResult(
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
                search_keywords=lora.get('search_keywords'),
                trigger_prompt=lora.get('trigger_prompt')
            ))

        # 按相似度排序
        response.sort(key=lambda x: x.similarity, reverse=True)

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to search LORAs: {e}")
        raise HTTPException(500, f"搜索失败: {str(e)}")
