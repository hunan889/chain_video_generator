"""
姿势图片API路由 - 展示所有图片+元数据(prompt/model)
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pathlib import Path
from typing import List, Dict
from pydantic import BaseModel
import json
import sqlite3
from api.middleware.auth import verify_api_key

router = APIRouter()

POSE_DIR = Path(__file__).parent.parent.parent / "data" / "pose_references"
PROJECT_ROOT = Path(__file__).parent.parent.parent
POSE_DB_PATH = PROJECT_ROOT / "data" / "wan22.db"

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.avi'}
ALL_MEDIA = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

MEDIA_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.webp': 'image/webp', '.mp4': 'video/mp4', '.webm': 'video/webm',
    '.mov': 'video/quicktime', '.avi': 'video/x-msvideo',
}


@router.get("/api/pose-images")
async def get_pose_images():
    """获取所有姿势图片列表，包含元数据"""
    if not POSE_DIR.exists():
        return {"images": [], "total": 0}

    images = []

    for pose_dir in sorted(POSE_DIR.iterdir()):
        if not pose_dir.is_dir():
            continue
        pose_name = pose_dir.name

        # 读取元数据
        meta_file = pose_dir / "_metadata.json"
        metadata = {}
        if meta_file.exists():
            try:
                with open(meta_file, 'r') as f:
                    metadata = json.load(f)
            except Exception:
                pass

        for f in sorted(pose_dir.iterdir()):
            if f.name.startswith("_"):
                continue
            if f.suffix.lower() not in ALL_MEDIA:
                continue

            file_size = f.stat().st_size
            size_kb = file_size / 1024
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
            is_video = f.suffix.lower() in VIDEO_EXTENSIONS

            # 从文件名提取ID匹配元数据
            # 文件名格式: missionary_12345.jpeg
            parts = f.stem.rsplit("_", 1)
            img_id = parts[-1] if len(parts) > 1 else ""
            meta = metadata.get(img_id, {})

            images.append({
                "filename": f.name,
                "pose": pose_name,
                "url": f"/pose-files/{pose_name}/{f.name}",
                "size": size_str,
                "type": "video" if is_video else "image",
                "prompt": meta.get("prompt", ""),
                "model": meta.get("model", ""),
                "search_tag": meta.get("search_tag", ""),
                "civitai_id": meta.get("civitai_id", ""),
            })

    return {"images": images, "total": len(images)}


@router.get("/pose-files/{pose}/{filename}")
async def serve_pose_file(pose: str, filename: str):
    """提供姿势图片/视频文件"""
    file_path = POSE_DIR / pose / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    media_type = MEDIA_TYPES.get(ext, "application/octet-stream")
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=86400",  # 缓存1天
            "ETag": f'"{file_path.stat().st_mtime}"'
        }
    )


# ========== Pose Association API ==========

class PoseImageAssociationRequest(BaseModel):
    """姿势图片关联请求"""
    resource_path: str
    pose_keys: List[str]


@router.get("/api/v1/pose-images/associations")
async def get_pose_image_associations(
    resource_path: str,
    _: str = Depends(verify_api_key)
):
    """获取姿势图片关联的姿势列表"""
    try:
        sqlite_conn = sqlite3.connect(str(POSE_DB_PATH))
        sqlite_conn.row_factory = sqlite3.Row
        sqlite_cursor = sqlite_conn.cursor()

        sqlite_cursor.execute("""
            SELECT p.id, p.pose_key, p.name_cn as pose_name, p.name_en
            FROM pose_reference_images pri
            JOIN poses p ON pri.pose_id = p.id
            WHERE pri.image_url = ?
        """, (resource_path,))

        associations = [dict(row) for row in sqlite_cursor.fetchall()]

        sqlite_cursor.close()
        sqlite_conn.close()

        return {"associations": associations}

    except Exception as e:
        raise HTTPException(500, f"查询失败: {str(e)}")


@router.put("/api/v1/pose-images/associations")
async def update_pose_image_associations(
    request: PoseImageAssociationRequest,
    _: str = Depends(verify_api_key)
):
    """更新姿势图片关联的姿势（替换所有关联）"""
    try:
        sqlite_conn = sqlite3.connect(str(POSE_DB_PATH))
        sqlite_cursor = sqlite_conn.cursor()

        # 删除现有关联
        sqlite_cursor.execute("DELETE FROM pose_reference_images WHERE image_url = ?", (request.resource_path,))

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
                """, (pose_id, request.resource_path))

        sqlite_conn.commit()
        sqlite_cursor.close()
        sqlite_conn.close()

        return {"success": True, "resource_path": request.resource_path, "pose_count": len(request.pose_keys)}

    except Exception as e:
        raise HTTPException(500, f"更新失败: {str(e)}")

