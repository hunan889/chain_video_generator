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


# Endpoints removed - use /recommend instead
# Old /search/resources and /search/loras endpoints have been merged into /recommend
