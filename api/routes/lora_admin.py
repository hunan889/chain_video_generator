"""
LORA管理API路由
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from api.middleware.auth import verify_api_key
from api.services.lora_classifier import get_lora_classifier
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


class CategorySuggestionResponse(BaseModel):
    category: Optional[str]
    confidence: float
    reasoning: str
    alternative: Optional[dict] = None


class LoraUpdateRequest(BaseModel):
    category: Optional[str] = None
    quality_score: Optional[int] = None
    custom_tags: Optional[list[str]] = None
    enabled: Optional[bool] = None
    trigger_prompt: Optional[str] = None
    search_keywords: Optional[str] = None


class BatchSuggestResponse(BaseModel):
    lora_id: int
    name: str
    category: Optional[str]
    confidence: float
    reasoning: str


@router.post("/admin/loras/{lora_id}/suggest-category", response_model=CategorySuggestionResponse)
async def suggest_lora_category(lora_id: int, _=Depends(verify_api_key)):
    """为单个LORA建议分类"""
    try:
        # 查询LORA信息
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        cursor.execute("""
            SELECT id, name, file, description, tags, trigger_words
            FROM lora_metadata
            WHERE id = %s
        """, (lora_id,))

        lora = cursor.fetchone()
        cursor.close()
        conn.close()

        if not lora:
            raise HTTPException(404, f"LORA #{lora_id} not found")

        # 使用分类器
        classifier = get_lora_classifier()
        result = await classifier.classify(lora)

        return CategorySuggestionResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to suggest category for LORA #{lora_id}: {e}")
        raise HTTPException(500, f"分类失败: {str(e)}")


@router.post("/admin/loras/sync-image-lora")
async def sync_image_lora(lora_id: str, name: str, file: str, filepath: str, _=Depends(verify_api_key)):
    """同步图片LORA到数据库（首次编辑时调用）"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # Insert or update
        cursor.execute("""
            INSERT INTO image_lora_metadata (id, name, file, filepath, enabled)
            VALUES (%s, %s, %s, %s, 0)
            ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            file = VALUES(file)
        """, (lora_id, name, file, filepath))

        conn.commit()
        cursor.close()
        conn.close()

        return {"success": True, "lora_id": lora_id}

    except Exception as e:
        logger.error(f"Failed to sync image LORA {lora_id}: {e}")
        raise HTTPException(500, f"同步失败: {str(e)}")


@router.patch("/admin/loras/{lora_id}")
async def update_lora_metadata(lora_id: str, req: LoraUpdateRequest, _=Depends(verify_api_key)):
    """更新LORA元数据"""
    try:
        # Check if it's an image LORA (string ID starting with 'img_')
        if isinstance(lora_id, str) and lora_id.startswith('img_'):
            # Image LORA - use image_lora_metadata table
            conn = pymysql.connect(**DB_CONFIG)
            cursor = conn.cursor()

            # Build update/insert statement
            updates = []
            params = []

            if req.category is not None:
                updates.append("category = %s")
                params.append(req.category)

            if req.quality_score is not None:
                if not (1 <= req.quality_score <= 10):
                    raise HTTPException(400, "quality_score must be between 1 and 10")
                updates.append("quality_score = %s")
                params.append(req.quality_score)

            if req.custom_tags is not None:
                import json
                updates.append("tags = %s")
                params.append(json.dumps(req.custom_tags))

            if req.enabled is not None:
                updates.append("enabled = %s")
                params.append(req.enabled)

            if req.trigger_prompt is not None:
                updates.append("trigger_prompt = %s")
                params.append(req.trigger_prompt)

            if req.search_keywords is not None:
                updates.append("search_keywords = %s")
                params.append(req.search_keywords)

            if not updates:
                cursor.close()
                conn.close()
                raise HTTPException(400, "No fields to update")

            # Try to update first
            params_update = params.copy()
            params_update.append(lora_id)
            sql = f"UPDATE image_lora_metadata SET {', '.join(updates)} WHERE id = %s"
            cursor.execute(sql, params_update)

            if cursor.rowcount == 0:
                # Record doesn't exist, insert a placeholder
                # We'll need name, file, filepath - these should be passed or we skip insert
                logger.warning(f"Image LORA {lora_id} not found in database, skipping insert")
                cursor.close()
                conn.close()
                return {"success": True, "lora_id": lora_id, "message": "Metadata cached, will persist on next scan"}

            conn.commit()
            cursor.close()
            conn.close()

            return {"success": True, "lora_id": lora_id, "updated_fields": len(updates)}

        # Video LORA - update database
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # 构建更新语句
        updates = []
        params = []

        if req.category is not None:
            updates.append("category = %s")
            params.append(req.category)

        if req.quality_score is not None:
            if not (1 <= req.quality_score <= 10):
                raise HTTPException(400, "quality_score must be between 1 and 10")
            updates.append("quality_score = %s")
            params.append(req.quality_score)

        if req.custom_tags is not None:
            import json
            updates.append("tags = %s")
            params.append(json.dumps(req.custom_tags))

        if req.enabled is not None:
            updates.append("enabled = %s")
            params.append(req.enabled)

        if req.trigger_prompt is not None:
            updates.append("trigger_prompt = %s")
            params.append(req.trigger_prompt)

        if req.search_keywords is not None:
            updates.append("search_keywords = %s")
            params.append(req.search_keywords)

        if not updates:
            raise HTTPException(400, "No fields to update")

        params.append(lora_id)

        sql = f"UPDATE lora_metadata SET {', '.join(updates)} WHERE id = %s"
        cursor.execute(sql, params)

        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            raise HTTPException(404, f"LORA #{lora_id} not found")

        conn.commit()
        cursor.close()
        conn.close()

        return {"success": True, "lora_id": lora_id, "updated_fields": len(updates)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update LORA #{lora_id}: {e}")
        raise HTTPException(500, f"更新失败: {str(e)}")


@router.post("/admin/loras/batch-suggest", response_model=list[BatchSuggestResponse])
async def batch_suggest_categories(lora_ids: Optional[list[int]] = None, _=Depends(verify_api_key)):
    """批量建议LORA分类"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 查询LORA
        if lora_ids:
            placeholders = ','.join(['%s'] * len(lora_ids))
            cursor.execute(f"""
                SELECT id, name, file, description, tags, trigger_words
                FROM lora_metadata
                WHERE id IN ({placeholders})
                ORDER BY id
            """, lora_ids)
        else:
            cursor.execute("""
                SELECT id, name, file, description, tags, trigger_words
                FROM lora_metadata
                WHERE category IS NULL
                ORDER BY id
            """)

        loras = cursor.fetchall()
        cursor.close()
        conn.close()

        if not loras:
            return []

        # 批量分类
        classifier = get_lora_classifier()
        results = await classifier.batch_classify(loras)

        # 构建响应
        response = []
        for lora, result in zip(loras, results):
            response.append(BatchSuggestResponse(
                lora_id=lora['id'],
                name=lora['name'],
                category=result.get('category'),
                confidence=result.get('confidence', 0.0),
                reasoning=result.get('reasoning', '')
            ))

        return response

    except Exception as e:
        logger.error(f"Failed to batch suggest categories: {e}")
        raise HTTPException(500, f"批量分类失败: {str(e)}")


@router.get("/admin/loras")
async def list_loras_admin(
    category: Optional[str] = None,
    unclassified: bool = False,
    lora_type: Optional[str] = None,  # 'video', 'image', or None for all
    _=Depends(verify_api_key)
):
    """管理端LORA列表（支持筛选）"""
    try:
        all_loras = []

        # 1. Get video LORAs from database
        if lora_type in [None, 'video']:
            conn = pymysql.connect(**DB_CONFIG)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 构建查询
            where_clauses = []
            params = []

            if unclassified:
                where_clauses.append("category IS NULL")
            elif category:
                where_clauses.append("category = %s")
                params.append(category)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor.execute(f"""
                SELECT id, name, file, category, description, tags, trigger_words, trigger_prompt,
                       mode, noise_stage, quality_score, civitai_id, preview_url, enabled, search_keywords
                FROM lora_metadata
                {where_sql}
                ORDER BY id
            """, params)

            video_loras = cursor.fetchall()
            cursor.close()
            conn.close()

            # Add type field
            for lora in video_loras:
                lora['lora_type'] = 'video'
                all_loras.append(lora)

        # 2. Get image LORAs from filesystem
        if lora_type in [None, 'image']:
            import os
            import glob

            lora_dir = '/home/gime/soft/lora/stable-diffusion-webui-forge/models/Lora'

            # Find all .safetensors files
            pattern = os.path.join(lora_dir, '**', '*.safetensors')
            lora_files = glob.glob(pattern, recursive=True)

            # Load existing metadata from database
            conn_img = pymysql.connect(**DB_CONFIG)
            cursor_img = conn_img.cursor(pymysql.cursors.DictCursor)
            cursor_img.execute("SELECT * FROM image_lora_metadata")
            img_metadata = {row['filepath']: row for row in cursor_img.fetchall()}
            cursor_img.close()
            conn_img.close()

            for idx, filepath in enumerate(lora_files):
                filename = os.path.basename(filepath)
                relative_path = os.path.relpath(filepath, lora_dir)

                # Extract category from path (e.g., Pony_NSFW)
                path_parts = relative_path.split(os.sep)
                file_category = path_parts[0] if len(path_parts) > 1 else None

                # Get metadata from database if exists
                metadata = img_metadata.get(relative_path, {})
                lora_id = metadata.get('id', f'img_{idx}')
                lora_category = metadata.get('category') or file_category
                lora_enabled = metadata.get('enabled', False)
                lora_quality = metadata.get('quality_score')
                lora_trigger_prompt = metadata.get('trigger_prompt')
                lora_description = metadata.get('description')
                lora_tags = metadata.get('tags', '[]')

                # Apply filters
                if category and lora_category != category:
                    continue
                if unclassified and lora_category is not None:
                    continue

                all_loras.append({
                    'id': lora_id,
                    'name': filename.replace('.safetensors', ''),
                    'file': filename,
                    'category': lora_category,
                    'description': lora_description,
                    'tags': lora_tags if isinstance(lora_tags, str) else '[]',
                    'trigger_words': '[]',
                    'trigger_prompt': lora_trigger_prompt,
                    'mode': 'image',
                    'noise_stage': 'single',
                    'quality_score': lora_quality,
                    'civitai_id': None,
                    'preview_url': None,
                    'enabled': lora_enabled,
                    'lora_type': 'image',
                    'filepath': relative_path
                })

        return all_loras

    except Exception as e:
        logger.error(f"Failed to list LORAs: {e}")
        raise HTTPException(500, f"查询失败: {str(e)}")
