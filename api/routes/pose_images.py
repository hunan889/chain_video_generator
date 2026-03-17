"""
姿势图片API路由 - 展示所有图片+元数据(prompt/model)
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path
from typing import List, Dict
import json

router = APIRouter()

POSE_DIR = Path(__file__).parent.parent.parent / "data" / "pose_references"

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
    return FileResponse(file_path, media_type=media_type)
