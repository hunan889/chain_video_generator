"""
资源管理API路由
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from api.middleware.auth import verify_api_key
import pymysql
import asyncio

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


class ResourceUpdateRequest(BaseModel):
    search_keywords: Optional[str] = None


def generate_image_search_keywords(url: str, prompt: str = "") -> str:
    """自动生成图片搜索关键词"""
    # 如果prompt较短且描述性强，直接使用
    if prompt and len(prompt) < 150 and len(prompt) > 20:
        return prompt

    keywords = []

    # 从prompt中提取关键词
    if prompt:
        words = prompt.lower().replace(',', ' ').split()
        stop_words = {'a', 'an', 'the', 'is', 'are', 'with', 'at', 'in', 'on', 'of', 'and', 'or'}
        keywords.extend([w for w in words if w not in stop_words and len(w) > 3][:15])

    # 从URL中提取文件名
    if not keywords and url:
        filename = url.split('/')[-1].split('?')[0]
        name_parts = filename.replace('_', ' ').replace('-', ' ').replace('.', ' ').split()
        keywords.extend([p for p in name_parts if len(p) > 3][:5])

    # 去重并限制数量
    keywords = list(dict.fromkeys(keywords))[:15]

    return ', '.join(keywords)


@router.post("/admin/resources/generate-search-keywords")
async def generate_search_keywords_for_favorites(_=Depends(verify_api_key)):
    """为收藏的图片生成搜索关键词"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 查询收藏的图片（没有search_keywords的）
        cursor.execute("""
            SELECT DISTINCT r.id, r.url, r.prompt
            FROM favorites f
            JOIN resources r ON f.resource_id = r.id
            WHERE (r.search_keywords IS NULL OR r.search_keywords = '')
            ORDER BY r.id
        """)

        resources = cursor.fetchall()

        if not resources:
            cursor.close()
            conn.close()
            return {
                "success": True,
                "message": "所有收藏图片都已有搜索关键词",
                "processed": 0,
                "total": 0
            }

        # 批量生成关键词
        updated_count = 0
        for resource in resources:
            resource_id = resource['id']
            url = resource['url']
            prompt = resource.get('prompt', '')

            # 生成关键词
            search_keywords = generate_image_search_keywords(url, prompt)

            if search_keywords:
                # 更新数据库
                cursor.execute("""
                    UPDATE resources
                    SET search_keywords = %s
                    WHERE id = %s
                """, (search_keywords, resource_id))
                updated_count += 1

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": f"成功为 {updated_count} 张图片生成搜索关键词",
            "processed": updated_count,
            "total": len(resources)
        }

    except Exception as e:
        logger.error(f"Failed to generate search keywords: {e}")
        raise HTTPException(500, f"生成失败: {str(e)}")


@router.patch("/admin/resources/{resource_id}")
async def update_resource_metadata(resource_id: int, req: ResourceUpdateRequest, _=Depends(verify_api_key)):
    """更新资源元数据"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # 构建更新语句
        updates = []
        params = []

        if req.search_keywords is not None:
            updates.append("search_keywords = %s")
            params.append(req.search_keywords)

        if not updates:
            raise HTTPException(400, "No fields to update")

        params.append(resource_id)

        sql = f"UPDATE resources SET {', '.join(updates)} WHERE id = %s"
        cursor.execute(sql, params)

        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            raise HTTPException(404, f"Resource #{resource_id} not found")

        conn.commit()
        cursor.close()
        conn.close()

        return {"success": True, "resource_id": resource_id, "updated_fields": len(updates)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update resource #{resource_id}: {e}")
        raise HTTPException(500, f"更新失败: {str(e)}")
