import shutil
import uuid
from pathlib import Path
from api.config import VIDEOS_DIR, UPLOADS_DIR

VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def save_video(data: bytes, extension: str = "mp4") -> str:
    filename = f"{uuid.uuid4().hex}.{extension}"
    path = VIDEOS_DIR / filename
    path.write_bytes(data)
    return filename


def save_upload(data: bytes, original_name: str) -> str:
    ext = Path(original_name).suffix or ".png"
    filename = f"{uuid.uuid4().hex}{ext}"
    path = UPLOADS_DIR / filename
    path.write_bytes(data)
    return filename


def get_video_path(filename: str) -> Path | None:
    path = VIDEOS_DIR / filename
    if path.exists():
        return path
    return None


def cleanup_video(filename: str):
    path = VIDEOS_DIR / filename
    if path.exists():
        path.unlink()
