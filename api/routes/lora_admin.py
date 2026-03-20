"""
LORA管理API路由
"""
import asyncio
import logging
import json
import os
import re
import uuid
from pathlib import Path
from typing import Dict, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from api.middleware.auth import verify_api_key
from api.services.lora_classifier import get_lora_classifier
from api.services.content_suggester import get_content_suggester
import aiohttp
import pymysql
from api.config import COMFYUI_PATH, CIVITAI_API_TOKEN

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

# 关键词对照文件路径
KEYWORDS_FILE = '/home/gime/soft/wan22-service/config/keywords.json'


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
    trigger_words: Optional[list[str]] = None


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

            if req.trigger_words is not None:
                import json
                updates.append("trigger_words = %s")
                params.append(json.dumps(req.trigger_words))

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

        if req.trigger_words is not None:
            import json
            updates.append("trigger_words = %s")
            params.append(json.dumps(req.trigger_words))

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
                       mode, noise_stage, quality_score, civitai_id, preview_url, enabled
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

                # Look for preview image
                preview_url = None
                base_name = filename.replace('.safetensors', '')
                preview_candidates = [
                    filepath.replace('.safetensors', '.preview.png'),
                    filepath.replace('.safetensors', '.png')
                ]
                for preview_path in preview_candidates:
                    if os.path.exists(preview_path):
                        preview_relative = os.path.relpath(preview_path, lora_dir)
                        preview_url = f'/api/v1/image-lora-preview/{preview_relative}'
                        break

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
                    'preview_url': preview_url,
                    'enabled': lora_enabled,
                    'lora_type': 'image',
                    'filepath': relative_path
                })

        return all_loras

    except Exception as e:
        logger.error(f"Failed to list LORAs: {e}")
        raise HTTPException(500, f"查询失败: {str(e)}")


@router.get("/image-lora-preview/{filepath:path}")
async def get_image_lora_preview(filepath: str):
    """获取图片LORA预览图"""
    import os
    from fastapi.responses import FileResponse

    lora_dir = '/home/gime/soft/lora/stable-diffusion-webui-forge/models/Lora'
    full_path = os.path.join(lora_dir, filepath)

    # Security check - ensure path is within lora_dir
    real_path = os.path.realpath(full_path)
    real_lora_dir = os.path.realpath(lora_dir)

    if not real_path.startswith(real_lora_dir):
        raise HTTPException(403, "Access denied")

    if not os.path.exists(real_path):
        raise HTTPException(404, "Preview image not found")

    return FileResponse(real_path, media_type="image/png")


# ========== Pose Association API ==========

import sqlite3
from pathlib import Path
from pydantic import BaseModel as _BaseModel
from typing import List as _List

_POSE_DB_PATH = Path(__file__).parent.parent.parent / "data" / "wan22.db"


class _PoseKeysRequest(_BaseModel):
    pose_keys: _List[str]


@router.get("/admin/loras/{lora_id}/poses")
async def get_lora_poses(lora_id: int, _=Depends(verify_api_key)):
    """获取LORA关联的姿势列表"""
    try:
        sqlite_conn = sqlite3.connect(str(_POSE_DB_PATH))
        sqlite_conn.row_factory = sqlite3.Row
        sqlite_cursor = sqlite_conn.cursor()

        sqlite_cursor.execute("""
            SELECT p.id, p.pose_key, p.name_cn as pose_name, p.name_en
            FROM pose_loras pl
            JOIN poses p ON pl.pose_id = p.id
            WHERE pl.lora_id = ?
        """, (lora_id,))

        associations = [dict(row) for row in sqlite_cursor.fetchall()]
        sqlite_cursor.close()
        sqlite_conn.close()

        return {"associations": associations}

    except Exception as e:
        raise HTTPException(500, f"查询失败: {str(e)}")


@router.put("/admin/loras/{lora_id}/poses")
async def update_lora_poses(lora_id: int, request: _PoseKeysRequest, _=Depends(verify_api_key)):
    """更新LORA关联的姿势（替换所有关联）"""
    try:
        sqlite_conn = sqlite3.connect(str(_POSE_DB_PATH))
        sqlite_cursor = sqlite_conn.cursor()

        # 删除现有关联
        sqlite_cursor.execute("DELETE FROM pose_loras WHERE lora_id = ?", (lora_id,))

        # 添加新关联
        for pose_key in request.pose_keys:
            sqlite_cursor.execute("SELECT id FROM poses WHERE pose_key = ?", (pose_key,))
            pose_row = sqlite_cursor.fetchone()

            if pose_row:
                pose_id = pose_row[0]
                sqlite_cursor.execute("""
                    INSERT INTO pose_loras (pose_id, lora_id, lora_type, noise_stage, is_default)
                    VALUES (?, ?, 'video', 'high', 0)
                """, (pose_id, lora_id))

        sqlite_conn.commit()
        sqlite_cursor.close()
        sqlite_conn.close()

        return {"success": True, "lora_id": lora_id, "pose_count": len(request.pose_keys)}

    except Exception as e:
        raise HTTPException(500, f"更新失败: {str(e)}")


# ─── LORA 下载功能 ───────────────────────────────────────────────

_IMAGE_LORA_DIR = Path('/home/gime/soft/lora/stable-diffusion-webui-forge/models/Lora')
_download_tasks: Dict[str, dict] = {}


class LoraDownloadRequest(BaseModel):
    url: str
    lora_type: str  # "video" or "image"
    name: Optional[str] = None


def _civitai_version_id(url: str) -> Optional[str]:
    """从CivitAI URL中提取版本ID"""
    m = re.search(r'civitai\.com/api/download/models/(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'modelVersionId=(\d+)', url)
    if m:
        return m.group(1)
    return None


async def _fetch_civitai_meta(version_id: str) -> dict:
    headers = {}
    if CIVITAI_API_TOKEN:
        headers['Authorization'] = f'Bearer {CIVITAI_API_TOKEN}'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'https://civitai.com/api/v1/model-versions/{version_id}',
                headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception:
        pass
    return {}


async def _do_download(task_id: str, url: str, lora_type: str, name: Optional[str]):
    task = _download_tasks[task_id]
    try:
        # Determine save directory
        save_dir = _IMAGE_LORA_DIR if lora_type == 'image' else (COMFYUI_PATH / 'models' / 'loras')
        save_dir.mkdir(parents=True, exist_ok=True)

        # CivitAI: fetch metadata + build download URL
        civitai_meta = {}
        version_id = _civitai_version_id(url)
        if version_id:
            civitai_meta = await _fetch_civitai_meta(version_id)
            # Build authenticated download URL
            dl_url = f'https://civitai.com/api/download/models/{version_id}'
            if CIVITAI_API_TOKEN:
                dl_url += f'?token={CIVITAI_API_TOKEN}'
        else:
            dl_url = url

        # Resolve filename
        if name:
            filename = name if name.endswith('.safetensors') else name + '.safetensors'
        elif civitai_meta:
            # Use CivitAI model name
            model_name = civitai_meta.get('model', {}).get('name', 'lora')
            filename = re.sub(r'[^\w\-.]', '_', model_name)[:80] + '.safetensors'
        else:
            filename = f'lora_{task_id[:8]}.safetensors'

        task['filename'] = filename
        save_path = save_dir / filename

        # Stream download with progress
        headers = {}
        if CIVITAI_API_TOKEN and 'civitai.com' in dl_url:
            headers['Authorization'] = f'Bearer {CIVITAI_API_TOKEN}'

        async with aiohttp.ClientSession() as session:
            async with session.get(dl_url, headers=headers, timeout=aiohttp.ClientTimeout(total=3600)) as resp:
                if resp.status != 200:
                    task['status'] = 'error'
                    task['error'] = f'HTTP {resp.status}'
                    return

                # Try to get filename from Content-Disposition
                cd = resp.headers.get('Content-Disposition', '')
                cd_match = re.search(r'filename="?([^"\n]+)', cd)
                if cd_match and not name:
                    cd_filename = cd_match.group(1).strip()
                    if cd_filename:
                        filename = cd_filename
                        task['filename'] = filename
                        save_path = save_dir / filename

                total_size = int(resp.headers.get('Content-Length', 0))
                task['total_size'] = total_size
                downloaded = 0

                with open(save_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(1024 * 256):
                        f.write(chunk)
                        downloaded += len(chunk)
                        task['downloaded'] = downloaded
                        if total_size > 0:
                            task['progress'] = round(downloaded / total_size * 100, 1)
                        else:
                            task['progress'] = -1  # unknown size

        # Insert metadata into DB
        stem = Path(filename).stem
        display_name = name or (civitai_meta.get('model', {}).get('name') if civitai_meta else None) or stem
        description = civitai_meta.get('description', '') or ''
        preview_url = ''
        if civitai_meta.get('images'):
            preview_url = civitai_meta['images'][0].get('url', '')
        tags_raw = civitai_meta.get('trainedWords', []) if civitai_meta else []
        civitai_id = int(version_id) if version_id else None

        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()
        try:
            if lora_type == 'video':
                cursor.execute("""
                    INSERT INTO lora_metadata
                        (name, file, description, tags, trigger_words, preview_url, civitai_id, enabled)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
                    ON DUPLICATE KEY UPDATE name=VALUES(name), preview_url=VALUES(preview_url)
                """, (
                    display_name, stem,
                    description[:2000] if description else '',
                    json.dumps(tags_raw), json.dumps(tags_raw),
                    preview_url, civitai_id
                ))
            else:
                cursor.execute("""
                    INSERT INTO image_lora_metadata
                        (name, file, filepath, description, tags, preview_url, enabled)
                    VALUES (%s, %s, %s, %s, %s, %s, 1)
                    ON DUPLICATE KEY UPDATE name=VALUES(name), preview_url=VALUES(preview_url)
                """, (
                    display_name, stem, str(save_path),
                    description[:2000] if description else '',
                    json.dumps(tags_raw),
                    preview_url
                ))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

        task['status'] = 'done'
        task['progress'] = 100
        logger.info(f"LORA download complete: {filename}")

    except Exception as e:
        logger.error(f"LORA download failed [{task_id}]: {e}")
        task['status'] = 'error'
        task['error'] = str(e)


@router.post("/loras/download-url")
async def start_lora_download(
    request: LoraDownloadRequest,
    background_tasks: BackgroundTasks,
    _=Depends(verify_api_key)
):
    """从URL下载LORA文件（支持CivitAI及任意直链）"""
    if request.lora_type not in ('video', 'image'):
        raise HTTPException(400, "lora_type must be 'video' or 'image'")

    task_id = uuid.uuid4().hex
    _download_tasks[task_id] = {
        'task_id': task_id,
        'status': 'pending',
        'progress': 0,
        'filename': '',
        'downloaded': 0,
        'total_size': 0,
        'error': None,
    }
    background_tasks.add_task(_do_download, task_id, request.url, request.lora_type, request.name)
    return {'task_id': task_id}


@router.get("/loras/download-url/{task_id}")
async def get_lora_download_progress(task_id: str, _=Depends(verify_api_key)):
    """查询LORA下载进度（download-url任务）"""
    task = _download_tasks.get(task_id)
    if not task:
        raise HTTPException(404, "下载任务不存在")
    return task
