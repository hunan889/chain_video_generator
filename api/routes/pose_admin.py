"""
姿势关联管理API
用于将资源和LORA关联到姿势
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import pymysql
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "wan22.db"

# MySQL配置
MYSQL_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}


class PoseAssociationRequest(BaseModel):
    """姿势关联请求"""
    pose_id: int
    angle: Optional[str] = None
    style: Optional[str] = None
    is_default: bool = False


class PoseReferenceImageRequest(BaseModel):
    """添加姿势首帧图请求"""
    pose_id: int
    resource_id: int
    angle: Optional[str] = None
    style: Optional[str] = None
    is_default: bool = False
    quality_score: Optional[float] = None


class PoseLoraRequest(BaseModel):
    """添加姿势LORA请求"""
    pose_id: int
    lora_id: int
    lora_type: str  # 'image' or 'video'
    noise_stage: Optional[str] = None
    trigger_words: Optional[str] = None
    recommended_weight: float = 1.0
    is_default: bool = False


def _get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@router.post("/admin/poses/reference-images")
async def add_pose_reference_image(request: PoseReferenceImageRequest):
    """
    将资源添加为姿势的首帧图

    示例:
    ```
    POST /api/v1/admin/poses/reference-images
    {
        "pose_id": 3,
        "resource_id": 32357,
        "angle": "pov",
        "style": "realistic",
        "is_default": true,
        "quality_score": 0.9
    }
    ```
    """
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        # 从MySQL获取资源信息
        mysql_conn = pymysql.connect(**MYSQL_CONFIG)
        mysql_cursor = mysql_conn.cursor(pymysql.cursors.DictCursor)

        mysql_cursor.execute("""
        SELECT id, prompt, url, resource_type
        FROM resources
        WHERE id = %s
        """, (request.resource_id,))

        resource = mysql_cursor.fetchone()
        mysql_cursor.close()
        mysql_conn.close()

        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        # 如果设置为默认，先取消其他默认
        if request.is_default:
            cursor.execute("""
            UPDATE pose_reference_images
            SET is_default = 0
            WHERE pose_id = ?
            """, (request.pose_id,))

        # 插入首帧图记录
        cursor.execute("""
        INSERT INTO pose_reference_images
        (pose_id, image_url, angle, style, prompt, is_default, quality_score)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            request.pose_id,
            resource['url'],
            request.angle,
            request.style,
            resource['prompt'],
            1 if request.is_default else 0,
            request.quality_score
        ))

        conn.commit()

        return {
            "success": True,
            "message": "Reference image added successfully",
            "id": cursor.lastrowid
        }

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.post("/admin/poses/loras")
async def add_pose_lora(request: PoseLoraRequest):
    """
    将LORA关联到姿势

    示例:
    ```
    POST /api/v1/admin/poses/loras
    {
        "pose_id": 3,
        "lora_id": 40,
        "lora_type": "video",
        "noise_stage": "high",
        "trigger_words": "cowgirl, woman on top, riding",
        "recommended_weight": 1.0,
        "is_default": true
    }
    ```
    """
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        # 如果设置为默认，先取消其他默认
        if request.is_default:
            cursor.execute("""
            UPDATE pose_loras
            SET is_default = 0
            WHERE pose_id = ? AND lora_type = ? AND (noise_stage = ? OR noise_stage IS NULL)
            """, (request.pose_id, request.lora_type, request.noise_stage))

        # 检查是否已存在
        cursor.execute("""
        SELECT id FROM pose_loras
        WHERE pose_id = ? AND lora_id = ? AND lora_type = ?
        """, (request.pose_id, request.lora_id, request.lora_type))

        existing = cursor.fetchone()

        if existing:
            # 更新现有记录
            cursor.execute("""
            UPDATE pose_loras
            SET noise_stage = ?,
                trigger_words = ?,
                recommended_weight = ?,
                is_default = ?
            WHERE id = ?
            """, (
                request.noise_stage,
                request.trigger_words,
                request.recommended_weight,
                1 if request.is_default else 0,
                existing['id']
            ))
            record_id = existing['id']
        else:
            # 插入新记录（不需要lora_name，因为可以从MySQL查询）
            cursor.execute("""
            INSERT INTO pose_loras
            (pose_id, lora_id, lora_type, noise_stage, trigger_words, recommended_weight, is_default)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                request.pose_id,
                request.lora_id,
                request.lora_type,
                request.noise_stage,
                request.trigger_words,
                request.recommended_weight,
                1 if request.is_default else 0
            ))
            record_id = cursor.lastrowid

        conn.commit()

        return {
            "success": True,
            "message": "LORA associated successfully",
            "id": record_id
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


class PoseReferenceImageUpdateRequest(BaseModel):
    """更新参考图属性"""
    skip_reactor: Optional[bool] = None
    angle: Optional[str] = None
    style: Optional[str] = None


@router.patch("/admin/poses/reference-images/{image_id}")
async def update_pose_reference_image(image_id: int, request: PoseReferenceImageUpdateRequest):
    """更新参考图属性 (skip_reactor, angle, style 等)"""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        updates = []
        params = []
        if request.skip_reactor is not None:
            updates.append("skip_reactor = ?")
            params.append(1 if request.skip_reactor else 0)
        if request.angle is not None:
            updates.append("angle = ?")
            params.append(request.angle)
        if request.style is not None:
            updates.append("style = ?")
            params.append(request.style)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        params.append(image_id)
        cursor.execute(f"UPDATE pose_reference_images SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Reference image not found")
        return {"success": True, "message": "Updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


def _delete_pose_file(image_url: str):
    """Delete pose reference file from local disk or COS CDN."""
    if not image_url:
        return False

    # Local file
    if image_url.startswith('/pose-files/'):
        rel_path = image_url[len('/pose-files/'):]
        local_path = PROJECT_ROOT / "data" / "pose_references" / rel_path
        if local_path.exists():
            local_path.unlink()
            logger.info(f"Deleted local pose file: {local_path}")
            return True
        return False

    # COS CDN file
    try:
        from api.services.cos_client import parse_cos_url, delete_file
        parsed = parse_cos_url(image_url)
        if parsed:
            subdir, filename = parsed
            delete_file(subdir, filename)
            return True
    except Exception as e:
        logger.warning(f"Failed to delete COS file {image_url}: {e}")

    return False


@router.delete("/admin/poses/reference-images/{image_id}")
async def remove_pose_reference_image(image_id: int):
    """删除姿势首帧图关联及磁盘文件"""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT image_url FROM pose_reference_images WHERE id = ?", (image_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Reference image not found")

        _delete_pose_file(row['image_url'])

        cursor.execute("DELETE FROM pose_reference_images WHERE id = ?", (image_id,))
        conn.commit()

        return {"success": True, "message": "Reference image removed"}

    finally:
        conn.close()


class BatchDeleteRequest(BaseModel):
    image_ids: List[int]


@router.post("/admin/poses/reference-images/batch-delete")
async def batch_delete_reference_images(request: BatchDeleteRequest):
    """批量删除姿势首帧图关联及磁盘文件"""
    if not request.image_ids:
        raise HTTPException(status_code=400, detail="No image IDs provided")

    conn = _get_connection()
    cursor = conn.cursor()

    try:
        placeholders = ','.join('?' for _ in request.image_ids)
        cursor.execute(
            f"SELECT id, image_url FROM pose_reference_images WHERE id IN ({placeholders})",
            request.image_ids
        )
        rows = cursor.fetchall()

        deleted_files = 0
        for row in rows:
            if _delete_pose_file(row['image_url']):
                deleted_files += 1

        cursor.execute(
            f"DELETE FROM pose_reference_images WHERE id IN ({placeholders})",
            request.image_ids
        )
        conn.commit()

        return {
            "success": True,
            "message": f"Deleted {cursor.rowcount} records, {deleted_files} files",
            "deleted_count": cursor.rowcount,
            "deleted_files": deleted_files
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


class BatchReassignRequest(BaseModel):
    image_ids: List[int]
    target_pose_id: int
    mode: str = "move"  # "move" = reassign, "copy" = keep original + add to target


@router.post("/admin/poses/reference-images/batch-reassign")
async def batch_reassign_reference_images(request: BatchReassignRequest):
    """批量将参考图移动或复制到另一个姿势"""
    if not request.image_ids:
        raise HTTPException(status_code=400, detail="No image IDs provided")
    if request.mode not in ("move", "copy"):
        raise HTTPException(status_code=400, detail="mode must be 'move' or 'copy'")

    conn = _get_connection()
    cursor = conn.cursor()

    try:
        # Verify target pose exists
        cursor.execute("SELECT id, pose_key FROM poses WHERE id = ?", (request.target_pose_id,))
        target = cursor.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Target pose not found")

        placeholders = ','.join('?' for _ in request.image_ids)
        cursor.execute(
            f"SELECT id, pose_id, image_url, angle, style, prompt, model, is_default, quality_score "
            f"FROM pose_reference_images WHERE id IN ({placeholders})",
            request.image_ids
        )
        rows = cursor.fetchall()

        if not rows:
            raise HTTPException(status_code=404, detail="No images found")

        moved = 0
        copied = 0
        skipped = 0

        for row in rows:
            # Skip if already belongs to target
            if row['pose_id'] == request.target_pose_id:
                skipped += 1
                continue

            if request.mode == "move":
                cursor.execute(
                    "UPDATE pose_reference_images SET pose_id = ?, is_default = 0 WHERE id = ?",
                    (request.target_pose_id, row['id'])
                )
                moved += 1
            else:  # copy
                # Check if same URL already exists on target
                cursor.execute(
                    "SELECT id FROM pose_reference_images WHERE pose_id = ? AND image_url = ?",
                    (request.target_pose_id, row['image_url'])
                )
                if cursor.fetchone():
                    skipped += 1
                    continue

                cursor.execute(
                    "INSERT INTO pose_reference_images "
                    "(pose_id, image_url, angle, style, prompt, model, is_default, quality_score) "
                    "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                    (request.target_pose_id, row['image_url'], row['angle'],
                     row['style'], row['prompt'], row['model'], row['quality_score'])
                )
                copied += 1

        conn.commit()

        action = "moved" if request.mode == "move" else "copied"
        count = moved if request.mode == "move" else copied
        return {
            "success": True,
            "message": f"{action} {count} images to {target['pose_key']}, skipped {skipped}",
            "count": count,
            "skipped": skipped
        }

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


class PoseLoraUpdateRequest(BaseModel):
    recommended_weight: Optional[float] = None


@router.patch("/admin/poses/loras/{association_id}")
async def update_pose_lora(association_id: int, request: PoseLoraUpdateRequest):
    """更新姿势LORA关联属性（如 recommended_weight）"""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        updates = []
        params = []
        if request.recommended_weight is not None:
            updates.append("recommended_weight = ?")
            params.append(request.recommended_weight)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        params.append(association_id)
        cursor.execute(f"UPDATE pose_loras SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="LORA association not found")
        return {"success": True, "message": "Updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.delete("/admin/poses/loras/{association_id}")
async def remove_pose_lora(association_id: int):
    """删除姿势LORA关联"""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM pose_loras WHERE id = ?", (association_id,))
        conn.commit()

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="LORA association not found")

        return {"success": True, "message": "LORA association removed"}

    finally:
        conn.close()


@router.get("/admin/resources/{resource_id}/pose-associations")
async def get_resource_pose_associations(resource_id: int):
    """获取资源的姿势关联"""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        # 先从MySQL获取资源URL
        mysql_conn = pymysql.connect(**MYSQL_CONFIG)
        mysql_cursor = mysql_conn.cursor(pymysql.cursors.DictCursor)

        mysql_cursor.execute("SELECT url FROM resources WHERE id = %s", (resource_id,))
        resource = mysql_cursor.fetchone()
        mysql_cursor.close()
        mysql_conn.close()

        if not resource:
            return {"associations": []}

        # 从SQLite查询关联
        cursor.execute("""
        SELECT pri.id, pri.pose_id, p.pose_key, p.name_cn, pri.angle, pri.style, pri.is_default
        FROM pose_reference_images pri
        JOIN poses p ON pri.pose_id = p.id
        WHERE pri.image_url = ?
        """, (resource['url'],))

        associations = [dict(row) for row in cursor.fetchall()]

        return {"associations": associations}

    finally:
        conn.close()


@router.get("/admin/loras/{lora_id}/pose-associations")
async def get_lora_pose_associations(lora_id: int):
    """获取LORA的姿势关联"""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        SELECT pl.id, pl.pose_id, p.pose_key, p.name_cn, pl.lora_type, pl.noise_stage, pl.is_default
        FROM pose_loras pl
        JOIN poses p ON pl.pose_id = p.id
        WHERE pl.lora_id = ?
        """, (lora_id,))

        associations = [dict(row) for row in cursor.fetchall()]

        return {"associations": associations}

    finally:
        conn.close()


class CreatePoseRequest(BaseModel):
    """创建姿势请求"""
    pose_key: str
    name_en: str
    name_cn: str
    description: Optional[str] = None
    difficulty: Optional[str] = "medium"
    category: Optional[str] = "other"


class UpdatePoseRequest(BaseModel):
    """更新姿势请求"""
    name_en: Optional[str] = None
    name_cn: Optional[str] = None
    description: Optional[str] = None
    difficulty: Optional[str] = None
    category: Optional[str] = None
    enabled: Optional[bool] = None


@router.post("/admin/poses")
async def create_pose(request: CreatePoseRequest):
    """创建新姿势"""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        INSERT INTO poses (pose_key, name_en, name_cn, description, difficulty, category, enabled)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (
            request.pose_key,
            request.name_en,
            request.name_cn,
            request.description,
            request.difficulty,
            request.category
        ))

        conn.commit()

        return {
            "success": True,
            "message": "Pose created successfully",
            "id": cursor.lastrowid
        }

    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Pose key already exists")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.put("/admin/poses/{pose_id}")
async def update_pose(pose_id: int, request: UpdatePoseRequest):
    """更新姿势信息"""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        updates = []
        params = []

        if request.name_en is not None:
            updates.append("name_en = ?")
            params.append(request.name_en)
        if request.name_cn is not None:
            updates.append("name_cn = ?")
            params.append(request.name_cn)
        if request.description is not None:
            updates.append("description = ?")
            params.append(request.description)
        if request.difficulty is not None:
            updates.append("difficulty = ?")
            params.append(request.difficulty)
        if request.category is not None:
            updates.append("category = ?")
            params.append(request.category)
        if request.enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if request.enabled else 0)

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(pose_id)
        cursor.execute(f"""
        UPDATE poses
        SET {', '.join(updates)}
        WHERE id = ?
        """, params)

        conn.commit()

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Pose not found")

        return {"success": True, "message": "Pose updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.delete("/admin/poses/{pose_id}")
async def delete_pose(pose_id: int):
    """删除姿势（软删除，设置enabled=0）"""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("UPDATE poses SET enabled = 0 WHERE id = ?", (pose_id,))
        conn.commit()

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Pose not found")

        return {"success": True, "message": "Pose deleted successfully"}

    finally:
        conn.close()


class AutoAssociateRequest(BaseModel):
    """自动关联请求"""
    pose_id: int
    match_threshold: float = 0.3


@router.post("/admin/poses/{pose_id}/auto-associate")
async def auto_associate_resources(pose_id: int, request: AutoAssociateRequest):
    """自动关联收藏的资源和启用的LORA到姿势"""
    conn = _get_connection()
    cursor = conn.cursor()

    try:
        # 获取姿势信息
        cursor.execute("SELECT pose_key, name_en, name_cn FROM poses WHERE id = ?", (pose_id,))
        pose = cursor.fetchone()
        if not pose:
            raise HTTPException(status_code=404, detail="Pose not found")

        # 连接MySQL
        mysql_conn = pymysql.connect(**MYSQL_CONFIG)
        mysql_cursor = mysql_conn.cursor(pymysql.cursors.DictCursor)

        associated_resources = 0
        associated_loras = 0

        # 查找收藏的资源
        mysql_cursor.execute("""
        SELECT r.id, r.prompt, r.url, r.resource_type
        FROM resources r
        INNER JOIN favorites f ON r.id = f.resource_id
        """)

        resources = mysql_cursor.fetchall()

        for resource in resources:
            # 检查是否已关联
            cursor.execute("""
            SELECT id FROM pose_reference_images
            WHERE pose_id = ? AND image_url = ?
            """, (pose_id, resource['url']))

            if not cursor.fetchone():
                # 添加关联
                cursor.execute("""
                INSERT INTO pose_reference_images
                (pose_id, image_url, prompt)
                VALUES (?, ?, ?)
                """, (pose_id, resource['url'], resource['prompt']))
                associated_resources += 1

        # 查找启用的LORA
        mysql_cursor.execute("""
        SELECT id, name, trigger_words, mode, noise_stage
        FROM lora_metadata
        WHERE enabled = 1
        """)

        loras = mysql_cursor.fetchall()

        for lora in loras:
            # 确定LORA类型
            lora_type = 'video'
            if lora['mode'] == 'I2V':
                lora_type = 'image'

            # 检查是否已关联
            cursor.execute("""
            SELECT id FROM pose_loras
            WHERE pose_id = ? AND lora_id = ? AND lora_type = ?
            """, (pose_id, lora['id'], lora_type))

            if not cursor.fetchone():
                # 提取trigger_words
                trigger_words_str = ''
                if lora['trigger_words']:
                    import json
                    try:
                        tw_list = json.loads(lora['trigger_words']) if isinstance(lora['trigger_words'], str) else lora['trigger_words']
                        if isinstance(tw_list, list):
                            trigger_words_str = ' '.join(tw_list)
                    except:
                        pass

                # 添加关联
                cursor.execute("""
                INSERT INTO pose_loras
                (pose_id, lora_id, lora_type, trigger_words, noise_stage, recommended_weight)
                VALUES (?, ?, ?, ?, ?, 1.0)
                """, (pose_id, lora['id'], lora_type, trigger_words_str, lora['noise_stage']))
                associated_loras += 1

        mysql_cursor.close()
        mysql_conn.close()
        conn.commit()

        return {
            "success": True,
            "message": f"Auto-associated {associated_resources} resources and {associated_loras} LORAs",
            "associated_resources": associated_resources,
            "associated_loras": associated_loras
        }

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
