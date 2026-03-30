"""
资源和标签管理 API
"""
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File, Form
from typing import List, Optional
from pydantic import BaseModel
import pymysql
import sqlite3
import uuid
import logging
from pathlib import Path
from api.middleware.auth import verify_api_key
from api.config import UPLOADS_DIR, COS_ENABLED

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["resources"])

PROJECT_ROOT = Path(__file__).parent.parent.parent
POSE_DB_PATH = PROJECT_ROOT / "data" / "wan22.db"

# 数据库配置
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}

def get_db():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)

# Pydantic 模型
class Tag(BaseModel):
    tag_id: int
    name: str
    category: Optional[str]
    source: str
    confidence: float
    usage_count: int

class Resource(BaseModel):
    id: int
    resource_type: str
    url: str
    prompt: Optional[str]
    trigger_prompt: Optional[str] = None
    tags: List[Tag]
    is_favorited: Optional[bool] = False

class AddTagRequest(BaseModel):
    tag_name: str
    category: Optional[str] = None

class AddFavoriteRequest(BaseModel):
    note: Optional[str] = None

class ResourceListResponse(BaseModel):
    resources: List[Resource]
    total: int
    page: int
    page_size: int
    total_pages: int

@router.get("/resources", response_model=ResourceListResponse)
async def list_resources(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    resource_type: Optional[str] = None,
    tag: Optional[str] = None,
    prompt: Optional[str] = None,
    search: Optional[str] = None,
    _: str = Depends(verify_api_key)
):
    """获取资源列表"""
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        # 构建查询
        where_clauses = []
        params = []

        if resource_type:
            # Support comma-separated types
            types = [t.strip() for t in resource_type.split(',') if t.strip()]
            if len(types) == 1:
                where_clauses.append("r.resource_type = %s")
                params.append(types[0])
            elif len(types) > 1:
                placeholders = ','.join(['%s'] * len(types))
                where_clauses.append(f"r.resource_type IN ({placeholders})")
                params.extend(types)

        if tag:
            where_clauses.append("""
                EXISTS (
                    SELECT 1 FROM resource_tags rt
                    JOIN tags t ON rt.tag_id = t.id
                    WHERE rt.resource_id = r.id AND t.name = %s
                )
            """)
            params.append(tag)

        if prompt:
            where_clauses.append("r.prompt LIKE %s")
            params.append(f"%{prompt}%")

        if search:
            where_clauses.append("r.prompt LIKE %s")
            params.append(f"%{search}%")

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # 获取总数
        cursor.execute(f"SELECT COUNT(*) as total FROM resources r WHERE {where_sql}", params)
        total = cursor.fetchone()['total']

        # 获取资源（优先显示有标签的）
        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT r.*,
                   (SELECT COUNT(*) FROM resource_tags rt WHERE rt.resource_id = r.id) as tag_count
            FROM resources r
            WHERE {where_sql}
            ORDER BY tag_count DESC, r.id DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        resources = cursor.fetchall()

        if not resources:
            return {
                'resources': [],
                'total': total,
                'page': page,
                'page_size': page_size,
                'total_pages': (total + page_size - 1) // page_size
            }

        # 获取所有资源ID
        resource_ids = [r['id'] for r in resources]

        # 批量获取标签
        placeholders = ','.join(['%s'] * len(resource_ids))
        cursor.execute(f"""
            SELECT rt.resource_id, t.id as tag_id, t.name, t.category, t.usage_count,
                   rt.source, rt.confidence
            FROM resource_tags rt
            JOIN tags t ON rt.tag_id = t.id
            WHERE rt.resource_id IN ({placeholders})
        """, resource_ids)
        all_tags = cursor.fetchall()

        # 组织标签数据
        tags_by_resource = {}
        for tag in all_tags:
            rid = tag['resource_id']
            if rid not in tags_by_resource:
                tags_by_resource[rid] = []
            tags_by_resource[rid].append({
                'tag_id': tag['tag_id'],
                'name': tag['name'],
                'category': tag['category'],
                'usage_count': tag['usage_count'],
                'source': tag['source'],
                'confidence': tag['confidence']
            })

        # 批量检查收藏状态
        cursor.execute(f"""
            SELECT resource_id FROM favorites WHERE resource_id IN ({placeholders})
        """, resource_ids)
        favorited_ids = {row['resource_id'] for row in cursor.fetchall()}

        # 组装结果
        result = []
        for r in resources:
            result.append({
                'id': r['id'],
                'resource_type': r['resource_type'],
                'url': r['url'],
                'prompt': r['prompt'],
                'trigger_prompt': r.get('trigger_prompt'),
                'tags': tags_by_resource.get(r['id'], []),
                'is_favorited': r['id'] in favorited_ids
            })

        return {
            'resources': result,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        }

    finally:
        conn.close()

@router.get("/resources/search", response_model=ResourceListResponse)
async def search_resources(
    tags: str = Query(..., description="逗号分隔的标签列表"),
    match_mode: str = Query("all", pattern="^(all|any)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    resource_type: Optional[str] = None,
    _: str = Depends(verify_api_key)
):
    """多标签搜索资源"""
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        tag_list = [t.strip().lower() for t in tags.split(',') if t.strip()]
        if not tag_list:
            raise HTTPException(status_code=400, detail="至少需要一个标签")

        # 构建查询
        if match_mode == "all":
            # 精确匹配：必须包含所有标签
            sql = """
                SELECT r.id, r.resource_type, r.url, r.prompt
                FROM resources r
                WHERE r.id IN (
                    SELECT rt.resource_id
                    FROM resource_tags rt
                    JOIN tags t ON rt.tag_id = t.id
                    WHERE t.name IN ({})
                    GROUP BY rt.resource_id
                    HAVING COUNT(DISTINCT t.name) = %s
                )
            """.format(','.join(['%s'] * len(tag_list)))
            params = tag_list + [len(tag_list)]
        else:
            # 模糊匹配：包含任意标签
            sql = """
                SELECT DISTINCT r.id, r.resource_type, r.url, r.prompt
                FROM resources r
                JOIN resource_tags rt ON r.id = rt.resource_id
                JOIN tags t ON rt.tag_id = t.id
                WHERE t.name IN ({})
            """.format(','.join(['%s'] * len(tag_list)))
            params = tag_list

        if resource_type:
            # Support comma-separated types
            types = [t.strip() for t in resource_type.split(',') if t.strip()]
            if len(types) == 1:
                sql += " AND r.resource_type = %s"
                params.append(types[0])
            elif len(types) > 1:
                placeholders = ','.join(['%s'] * len(types))
                sql += f" AND r.resource_type IN ({placeholders})"
                params.extend(types)

        # 获取总数
        count_sql = f"SELECT COUNT(*) as total FROM ({sql}) as subquery"
        cursor.execute(count_sql, params)
        total = cursor.fetchone()['total']

        # 获取资源
        offset = (page - 1) * page_size
        cursor.execute(f"{sql} ORDER BY r.id DESC LIMIT %s OFFSET %s", params + [page_size, offset])
        resources = cursor.fetchall()

        if not resources:
            return {
                'resources': [],
                'total': total,
                'page': page,
                'page_size': page_size,
                'total_pages': (total + page_size - 1) // page_size
            }

        # 获取所有资源ID
        resource_ids = [r['id'] for r in resources]

        # 批量获取标签
        placeholders = ','.join(['%s'] * len(resource_ids))
        cursor.execute(f"""
            SELECT rt.resource_id, t.id as tag_id, t.name, t.category, t.usage_count,
                   rt.source, rt.confidence
            FROM resource_tags rt
            JOIN tags t ON rt.tag_id = t.id
            WHERE rt.resource_id IN ({placeholders})
        """, resource_ids)
        all_tags = cursor.fetchall()

        # 组织标签数据
        tags_by_resource = {}
        for tag in all_tags:
            rid = tag['resource_id']
            if rid not in tags_by_resource:
                tags_by_resource[rid] = []
            tags_by_resource[rid].append({
                'tag_id': tag['tag_id'],
                'name': tag['name'],
                'category': tag['category'],
                'usage_count': tag['usage_count'],
                'source': tag['source'],
                'confidence': tag['confidence']
            })

        # 批量检查收藏状态
        cursor.execute(f"""
            SELECT resource_id FROM favorites WHERE resource_id IN ({placeholders})
        """, resource_ids)
        favorited_ids = {row['resource_id'] for row in cursor.fetchall()}

        # 组装结果
        result = []
        for r in resources:
            result.append({
                'id': r['id'],
                'resource_type': r['resource_type'],
                'url': r['url'],
                'prompt': r['prompt'],
                'trigger_prompt': r.get('trigger_prompt'),
                'tags': tags_by_resource.get(r['id'], []),
                'is_favorited': r['id'] in favorited_ids
            })

        return {
            'resources': result,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        }

    finally:
        conn.close()

@router.post("/resources/{resource_id}/tags")
async def add_tag(
    resource_id: int,
    request: AddTagRequest,
    _: str = Depends(verify_api_key)
):
    """添加标签到资源"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        # 获取或创建标签
        cursor.execute("SELECT id FROM tags WHERE name = %s", (request.tag_name,))
        result = cursor.fetchone()

        if result:
            tag_id = result[0]
        else:
            cursor.execute(
                "INSERT INTO tags (name, category, usage_count) VALUES (%s, %s, 0)",
                (request.tag_name, request.category)
            )
            tag_id = cursor.lastrowid

        # 关联资源和标签
        cursor.execute("""
            INSERT IGNORE INTO resource_tags (resource_id, tag_id, source, confidence)
            VALUES (%s, %s, 'manual', 1.0)
        """, (resource_id, tag_id))

        # 更新标签使用次数
        cursor.execute("UPDATE tags SET usage_count = usage_count + 1 WHERE id = %s", (tag_id,))

        conn.commit()
        return {"success": True, "tag_id": tag_id}

    finally:
        conn.close()

@router.delete("/resources/{resource_id}/tags/{tag_id}")
async def remove_tag(
    resource_id: int,
    tag_id: int,
    _: str = Depends(verify_api_key)
):
    """从资源移除标签"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "DELETE FROM resource_tags WHERE resource_id = %s AND tag_id = %s",
            (resource_id, tag_id)
        )

        # 更新标签使用次数
        cursor.execute("UPDATE tags SET usage_count = usage_count - 1 WHERE id = %s", (tag_id,))

        conn.commit()
        return {"success": True}

    finally:
        conn.close()

@router.get("/tags")
async def list_tags(
    category: Optional[str] = None,
    _: str = Depends(verify_api_key)
):
    """获取标签列表"""
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        if category:
            cursor.execute(
                "SELECT * FROM tags WHERE category = %s ORDER BY usage_count DESC",
                (category,)
            )
        else:
            cursor.execute("SELECT * FROM tags ORDER BY usage_count DESC")

        tags = cursor.fetchall()
        return tags

    finally:
        conn.close()

# ========== Favorites API ==========

# Pose Image Favorites (must be before /{resource_id} routes)
class AddPoseFavoriteRequest(BaseModel):
    """添加姿势图片收藏请求"""
    resource_path: str
    note: Optional[str] = None


@router.post("/favorites/pose-image")
async def add_pose_favorite(
    request: AddPoseFavoriteRequest,
    _: str = Depends(verify_api_key)
):
    """添加姿势图片收藏（基于路径）"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO favorites (resource_path, note)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE note = VALUES(note)
        """, (request.resource_path, request.note))
        conn.commit()
        return {"success": True, "favorite_id": cursor.lastrowid}
    finally:
        conn.close()


@router.delete("/favorites/pose-image")
async def remove_pose_favorite(
    resource_path: str,
    _: str = Depends(verify_api_key)
):
    """取消姿势图片收藏（基于路径）"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM favorites WHERE resource_path = %s", (resource_path,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.get("/favorites/pose-image/check")
async def check_pose_favorite(
    resource_path: str,
    _: str = Depends(verify_api_key)
):
    """检查姿势图片是否已收藏（基于路径）"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM favorites WHERE resource_path = %s", (resource_path,))
        result = cursor.fetchone()
        return {"is_favorited": result is not None}
    finally:
        conn.close()


# LORA Favorites (must be before /{resource_id} routes)
class AddLoraFavoriteRequest(BaseModel):
    """添加LORA收藏请求"""
    lora_id: int
    lora_type: str  # 'image' or 'video'
    note: Optional[str] = None


@router.post("/favorites/lora")
async def add_lora_favorite(
    request: AddLoraFavoriteRequest,
    _: str = Depends(verify_api_key)
):
    """添加LORA收藏"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO favorites (lora_id, lora_type, note)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE note = VALUES(note)
        """, (request.lora_id, request.lora_type, request.note))
        conn.commit()
        return {"success": True, "favorite_id": cursor.lastrowid}
    finally:
        conn.close()


@router.delete("/favorites/lora")
async def remove_lora_favorite(
    lora_id: int,
    lora_type: str,
    _: str = Depends(verify_api_key)
):
    """取消LORA收藏"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM favorites WHERE lora_id = %s AND lora_type = %s", (lora_id, lora_type))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()


@router.get("/favorites/lora/check")
async def check_lora_favorite(
    lora_id: int,
    lora_type: str,
    _: str = Depends(verify_api_key)
):
    """检查LORA是否已收藏"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM favorites WHERE lora_id = %s AND lora_type = %s", (lora_id, lora_type))
        result = cursor.fetchone()
        return {"is_favorited": result is not None}
    finally:
        conn.close()


@router.get("/favorites/stats")
async def get_favorites_stats(_: str = Depends(verify_api_key)):
    """获取收藏统计数据（所有类型）"""
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        # 一次查询获取所有统计
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN r.resource_type = 'image' THEN 1 ELSE 0 END) as image,
                SUM(CASE WHEN r.resource_type IN ('video', 'generated_video') THEN 1 ELSE 0 END) as video,
                SUM(CASE WHEN f.resource_path LIKE '/pose-files/%%' THEN 1 ELSE 0 END) as pose_image
            FROM favorites f
            LEFT JOIN resources r ON f.resource_id = r.id
        """)
        stats = cursor.fetchone()

        # 获取LORA统计
        cursor.execute("SELECT COUNT(*) as total FROM lora_metadata WHERE enabled = 1")
        lora_stats = cursor.fetchone()

        return {
            'total': (stats['total'] or 0) + (lora_stats['total'] or 0),
            'image': stats['image'] or 0,
            'video': stats['video'] or 0,
            'pose_image': stats['pose_image'] or 0,
            'video_lora': lora_stats['total'] or 0,
            'image_lora': 0
        }
    finally:
        conn.close()

@router.get("/favorites/all")
async def list_all_favorites(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    favorite_type: Optional[str] = Query(None, description="Filter by type: image, video, pose_image, image_lora, video_lora"),
    _: str = Depends(verify_api_key)
):
    """获取所有收藏（包括普通资源、姿势图片和LORA）"""

    # 如果是 LORA 类型，直接从 lora_metadata 查询
    if favorite_type in ['image_lora', 'video_lora']:
        return await list_lora_favorites(favorite_type, page, page_size)

    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        offset = (page - 1) * page_size

        # 构建WHERE条件
        where_clauses = []
        count_where = ""
        query_where = ""

        if favorite_type == 'image':
            where_clauses.append("(f.resource_id IS NOT NULL AND r.resource_type = 'image')")
        elif favorite_type == 'video':
            where_clauses.append("(f.resource_id IS NOT NULL AND r.resource_type IN ('video', 'generated_video'))")
        elif favorite_type == 'pose_image':
            where_clauses.append("(f.resource_path IS NOT NULL AND f.resource_path LIKE '/pose-files/%%')")

        if where_clauses:
            count_where = "WHERE " + " OR ".join(where_clauses)
            query_where = count_where

        # 获取总数
        count_sql = "SELECT COUNT(*) as total FROM favorites f LEFT JOIN resources r ON f.resource_id = r.id " + count_where
        cursor.execute(count_sql)
        total = cursor.fetchone()['total']

        # 获取所有收藏
        query_sql = """
            SELECT
                f.id,
                f.resource_id,
                f.resource_path,
                f.note,
                f.created_at,
                r.resource_type,
                r.url,
                r.prompt
            FROM favorites f
            LEFT JOIN resources r ON f.resource_id = r.id
            """ + query_where + """
            ORDER BY f.created_at DESC
            LIMIT %s OFFSET %s
        """
        cursor.execute(query_sql, (page_size, offset))

        favorites = cursor.fetchall()

        # 批量查询姿势关联（SQLite）
        resource_urls = [fav['url'] for fav in favorites if fav['resource_id'] and fav['url']]
        pose_paths = [fav['resource_path'] for fav in favorites if fav['resource_path']]
        all_urls = resource_urls + pose_paths

        pose_keys_map = {}  # url -> [pose_key, ...]
        if all_urls:
            try:
                sqlite_conn = sqlite3.connect(str(POSE_DB_PATH))
                sqlite_conn.row_factory = sqlite3.Row
                sqlite_cursor = sqlite_conn.cursor()
                placeholders = ','.join(['?'] * len(all_urls))
                sqlite_cursor.execute(f"""
                    SELECT pri.image_url, p.pose_key
                    FROM pose_reference_images pri
                    JOIN poses p ON pri.pose_id = p.id
                    WHERE pri.image_url IN ({placeholders})
                """, all_urls)
                for row in sqlite_cursor.fetchall():
                    url = row['image_url']
                    if url not in pose_keys_map:
                        pose_keys_map[url] = []
                    pose_keys_map[url].append(row['pose_key'])
                sqlite_cursor.close()
                sqlite_conn.close()
            except Exception:
                pass

        # 处理结果
        result_list = []
        for fav in favorites:
            if fav['resource_id']:
                # 普通资源（来自 MySQL resources 表）
                result_list.append({
                    'id': fav['id'],
                    'type': 'resource',
                    'resource_id': fav['resource_id'],
                    'resource_type': fav['resource_type'],
                    'url': fav['url'],
                    'prompt': fav['prompt'],
                    'note': fav['note'],
                    'created_at': fav['created_at'].isoformat() if fav['created_at'] else None,
                    'pose_keys': pose_keys_map.get(fav['url'], [])
                })
            elif fav['resource_path']:
                # 姿势图片（本地文件）
                path_parts = fav['resource_path'].split('/')
                pose_name = path_parts[2] if len(path_parts) > 2 else ''
                filename = path_parts[3] if len(path_parts) > 3 else ''
                resource_type = 'video' if filename.endswith(('.mp4', '.webm')) else 'image'

                result_list.append({
                    'id': fav['id'],
                    'type': 'pose_image',
                    'resource_path': fav['resource_path'],
                    'resource_type': resource_type,
                    'url': fav['resource_path'],
                    'pose': pose_name,
                    'filename': filename,
                    'note': fav['note'],
                    'created_at': fav['created_at'].isoformat() if fav['created_at'] else None,
                    'pose_keys': pose_keys_map.get(fav['resource_path'], [])
                })

        return {
            'favorites': result_list,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        }

    finally:
        conn.close()


async def list_lora_favorites(lora_type: str, page: int, page_size: int):
    """获取已启用的 LORA（enabled=1）"""
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        offset = (page - 1) * page_size

        # 确定 LORA 类型
        if lora_type == 'video_lora':
            # 视频 LORA 从 lora_metadata 表查询
            cursor.execute("SELECT COUNT(*) as total FROM lora_metadata WHERE enabled = 1")
            total = cursor.fetchone()['total']

            cursor.execute("""
                SELECT id, name, file, preview_url, description, trigger_words, trigger_prompt, category, 'video' as lora_type
                FROM lora_metadata
                WHERE enabled = 1
                ORDER BY id
                LIMIT %s OFFSET %s
            """, (page_size, offset))

            loras = cursor.fetchall()
            result_list = []

            # 批量查询LORA的姿势关联（SQLite）
            lora_ids = [l['id'] for l in loras]
            lora_pose_map = {}
            if lora_ids:
                try:
                    sqlite_conn = sqlite3.connect(str(POSE_DB_PATH))
                    sqlite_conn.row_factory = sqlite3.Row
                    sqlite_cursor = sqlite_conn.cursor()
                    placeholders = ','.join(['?'] * len(lora_ids))
                    sqlite_cursor.execute(f"""
                        SELECT pl.lora_id, p.pose_key
                        FROM pose_loras pl
                        JOIN poses p ON pl.pose_id = p.id
                        WHERE pl.lora_id IN ({placeholders})
                    """, lora_ids)
                    for row in sqlite_cursor.fetchall():
                        lid = row['lora_id']
                        if lid not in lora_pose_map:
                            lora_pose_map[lid] = []
                        lora_pose_map[lid].append(row['pose_key'])
                    sqlite_cursor.close()
                    sqlite_conn.close()
                except Exception:
                    pass

            for lora in loras:
                result_list.append({
                    'id': lora['id'],
                    'type': 'video_lora',
                    'lora_id': lora['id'],
                    'lora_type': 'video',
                    'name': lora['name'],
                    'file': lora['file'],
                    'preview_url': lora['preview_url'],
                    'description': lora['description'],
                    'trigger_words': lora['trigger_words'],
                    'trigger_prompt': lora['trigger_prompt'],
                    'category': lora['category'],
                    'created_at': None,
                    'pose_keys': lora_pose_map.get(lora['id'], [])
                })

        else:
            # 图片 LORA（暂时返回空，需要实现图片 LORA 的存储）
            total = 0
            result_list = []

        return {
            'favorites': result_list,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        }

    finally:
        conn.close()


# Resource Favorites (with {resource_id} parameter)

@router.post("/favorites/{resource_id}")
async def add_favorite(
    resource_id: int,
    request: AddFavoriteRequest,
    _: str = Depends(verify_api_key)
):
    """添加收藏"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO favorites (resource_id, note)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE note = VALUES(note)
        """, (resource_id, request.note))
        conn.commit()
        return {"success": True, "favorite_id": cursor.lastrowid}
    finally:
        conn.close()

@router.delete("/favorites/{resource_id}")
async def remove_favorite(
    resource_id: int,
    _: str = Depends(verify_api_key)
):
    """取消收藏"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM favorites WHERE resource_id = %s", (resource_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

@router.get("/favorites", response_model=ResourceListResponse)
async def list_favorites(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    resource_type: Optional[str] = None,
    search: Optional[str] = None,
    _: str = Depends(verify_api_key)
):
    """获取收藏列表"""
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        where_clauses = ["f.resource_id = r.id"]
        params = []

        if resource_type:
            types = [t.strip() for t in resource_type.split(',') if t.strip()]
            if len(types) == 1:
                where_clauses.append("r.resource_type = %s")
                params.append(types[0])
            elif len(types) > 1:
                placeholders = ','.join(['%s'] * len(types))
                where_clauses.append(f"r.resource_type IN ({placeholders})")
                params.extend(types)

        if search:
            where_clauses.append("r.prompt LIKE %s")
            params.append(f"%{search}%")

        where_sql = " AND ".join(where_clauses)

        # 获取总数
        cursor.execute(f"""
            SELECT COUNT(*) as total 
            FROM favorites f
            JOIN resources r ON {where_sql}
        """, params)
        total = cursor.fetchone()['total']

        # 获取收藏资源
        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT r.*, f.note, f.created_at as favorited_at,
                   (SELECT COUNT(*) FROM resource_tags rt WHERE rt.resource_id = r.id) as tag_count
            FROM favorites f
            JOIN resources r ON {where_sql}
            ORDER BY f.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        resources = cursor.fetchall()

        if not resources:
            return {
                'resources': [],
                'total': total,
                'page': page,
                'page_size': page_size,
                'total_pages': (total + page_size - 1) // page_size
            }

        # 获取所有资源ID
        resource_ids = [r['id'] for r in resources]

        # 批量获取标签
        placeholders = ','.join(['%s'] * len(resource_ids))
        cursor.execute(f"""
            SELECT rt.resource_id, t.id as tag_id, t.name, t.category, t.usage_count,
                   rt.source, rt.confidence
            FROM resource_tags rt
            JOIN tags t ON rt.tag_id = t.id
            WHERE rt.resource_id IN ({placeholders})
        """, resource_ids)
        all_tags = cursor.fetchall()

        # 组织标签数据
        tags_by_resource = {}
        for tag in all_tags:
            rid = tag['resource_id']
            if rid not in tags_by_resource:
                tags_by_resource[rid] = []
            tags_by_resource[rid].append({
                'tag_id': tag['tag_id'],
                'name': tag['name'],
                'category': tag['category'],
                'usage_count': tag['usage_count'],
                'source': tag['source'],
                'confidence': tag['confidence']
            })

        # 组装结果
        result = []
        for r in resources:
            result.append({
                'id': r['id'],
                'resource_type': r['resource_type'],
                'url': r['url'],
                'prompt': r['prompt'],
                'trigger_prompt': r.get('trigger_prompt'),
                'tags': tags_by_resource.get(r['id'], []),
                'is_favorited': True
            })

        return {
            'resources': result,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        }

    finally:
        conn.close()

@router.get("/favorites/check/{resource_id}")
async def check_favorite(
    resource_id: int,
    _: str = Depends(verify_api_key)
):
    """检查是否已收藏"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM favorites WHERE resource_id = %s", (resource_id,))
        result = cursor.fetchone()
        return {"is_favorited": result is not None}
    finally:
        conn.close()




# ========== Pose Association API ==========

class PoseKeysRequest(BaseModel):
    """姿势关联请求"""
    pose_keys: List[str]


@router.get("/resources/{resource_id}/poses")
async def get_resource_poses(
    resource_id: int,
    _: str = Depends(verify_api_key)
):
    """获取资源关联的姿势列表"""
    mysql_conn = get_db()
    mysql_cursor = mysql_conn.cursor(pymysql.cursors.DictCursor)

    try:
        # 获取资源URL
        mysql_cursor.execute("SELECT url FROM resources WHERE id = %s", (resource_id,))
        resource = mysql_cursor.fetchone()

        if not resource:
            raise HTTPException(404, "资源不存在")

        # 从SQLite查询关联
        sqlite_conn = sqlite3.connect(str(POSE_DB_PATH))
        sqlite_conn.row_factory = sqlite3.Row
        sqlite_cursor = sqlite_conn.cursor()

        sqlite_cursor.execute("""
            SELECT p.id, p.pose_key, p.name_cn as pose_name, p.name_en
            FROM pose_reference_images pri
            JOIN poses p ON pri.pose_id = p.id
            WHERE pri.image_url = ?
        """, (resource['url'],))

        associations = [dict(row) for row in sqlite_cursor.fetchall()]

        sqlite_cursor.close()
        sqlite_conn.close()

        return {"associations": associations}

    finally:
        mysql_cursor.close()
        mysql_conn.close()


@router.put("/resources/{resource_id}/poses")
async def update_resource_poses(
    resource_id: int,
    request: PoseKeysRequest,
    _: str = Depends(verify_api_key)
):
    """更新资源关联的姿势（替换所有关联）"""
    mysql_conn = get_db()
    mysql_cursor = mysql_conn.cursor(pymysql.cursors.DictCursor)

    try:
        # 获取资源URL
        mysql_cursor.execute("SELECT url FROM resources WHERE id = %s", (resource_id,))
        resource = mysql_cursor.fetchone()

        if not resource:
            raise HTTPException(404, "资源不存在")

        image_url = resource['url']

        # 操作SQLite
        sqlite_conn = sqlite3.connect(str(POSE_DB_PATH))
        sqlite_cursor = sqlite_conn.cursor()

        # 删除现有关联
        sqlite_cursor.execute("DELETE FROM pose_reference_images WHERE image_url = ?", (image_url,))

        # 添加新关联
        for pose_key in request.pose_keys:
            # 获取pose_id
            sqlite_cursor.execute("SELECT id FROM poses WHERE pose_key = ?", (pose_key,))
            pose_row = sqlite_cursor.fetchone()

            if pose_row:
                pose_id = pose_row[0]
                sqlite_cursor.execute("""
                    INSERT INTO pose_reference_images (pose_id, image_url, angle, style, is_default)
                    VALUES (?, ?, NULL, NULL, 0)
                """, (pose_id, image_url))

        sqlite_conn.commit()
        sqlite_cursor.close()
        sqlite_conn.close()

        return {"success": True, "resource_id": resource_id, "pose_count": len(request.pose_keys)}

    finally:
        mysql_cursor.close()
        mysql_conn.close()


@router.post("/resources/upload")
async def upload_resource(
    file: UploadFile = File(...),
    resource_type: str = Form(...),
    prompt: str = Form(None),
    _: str = Depends(verify_api_key)
):
    """上传图片或视频，自动添加到收藏"""
    IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'}
    VIDEO_EXTS = {'.mp4', '.webm', '.mov', '.avi'}

    original_name = file.filename or 'upload'
    ext = Path(original_name).suffix.lower()

    if resource_type == 'image' and ext not in IMAGE_EXTS:
        raise HTTPException(400, f"不支持的图片格式: {ext}")
    if resource_type == 'video' and ext not in VIDEO_EXTS:
        raise HTTPException(400, f"不支持的视频格式: {ext}")

    filename = f"{uuid.uuid4().hex}{ext}"
    local_path = UPLOADS_DIR / filename
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    with open(local_path, 'wb') as f:
        f.write(content)

    url = f"/uploads/{filename}"
    if COS_ENABLED:
        try:
            from api.services.cos_client import upload_file as cos_upload
            url = cos_upload(local_path, 'uploads', filename)
        except Exception as e:
            logger.warning(f"COS upload failed: {e}, using local path")

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO resources (resource_type, url, prompt, created_at) VALUES (%s, %s, %s, NOW())",
            (resource_type, url, prompt)
        )
        resource_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO favorites (resource_id, note) VALUES (%s, NULL)",
            (resource_id,)
        )
        conn.commit()
        return {"success": True, "resource_id": resource_id, "url": url}
    finally:
        cursor.close()
        conn.close()


@router.get("/favorites/pose-associated")
async def list_pose_associated_images(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: str = Depends(verify_api_key)
):
    """获取所有已关联姿势的图片（不限于手动收藏）"""
    try:
        sqlite_conn = sqlite3.connect(str(POSE_DB_PATH))
        sqlite_conn.row_factory = sqlite3.Row
        sqlite_cursor = sqlite_conn.cursor()

        sqlite_cursor.execute("SELECT COUNT(DISTINCT image_url) as total FROM pose_reference_images")
        total = sqlite_cursor.fetchone()['total']

        offset = (page - 1) * page_size
        sqlite_cursor.execute("""
            SELECT pri.image_url,
                   GROUP_CONCAT(p.pose_key, ',') as pose_keys,
                   GROUP_CONCAT(COALESCE(p.name_cn, p.name_en, p.pose_key), ',') as pose_names
            FROM pose_reference_images pri
            JOIN poses p ON pri.pose_id = p.id
            GROUP BY pri.image_url
            ORDER BY pri.image_url
            LIMIT ? OFFSET ?
        """, (page_size, offset))

        rows = sqlite_cursor.fetchall()
        sqlite_cursor.close()
        sqlite_conn.close()

        VIDEO_EXTS = {'.mp4', '.webm', '.mov', '.avi'}
        result = []
        for row in rows:
            image_url = row['image_url']
            pose_keys = [k for k in (row['pose_keys'] or '').split(',') if k]
            pose_names = [n for n in (row['pose_names'] or '').split(',') if n]
            resource_type = 'video' if Path(image_url).suffix.lower() in VIDEO_EXTS else 'image'
            result.append({
                'url': image_url,
                'resource_type': resource_type,
                'pose_keys': pose_keys,
                'pose_names': pose_names,
            })

        return {
            'items': result,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        }
    except Exception as e:
        raise HTTPException(500, f"查询失败: {str(e)}")


@router.get("/resources/{resource_id}")
async def get_resource(
    resource_id: int,
    _: str = Depends(verify_api_key)
):
    """获取单个资源详情"""
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        cursor.execute("SELECT * FROM resources WHERE id = %s", (resource_id,))
        resource = cursor.fetchone()

        if not resource:
            raise HTTPException(404, "资源不存在")

        return resource

    finally:
        cursor.close()
        conn.close()

