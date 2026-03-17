import asyncio
import logging
import uuid
from pathlib import Path
from api.config import VIDEOS_DIR, UPLOADS_DIR, COS_ENABLED

logger = logging.getLogger(__name__)

VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _sync_save_video(data: bytes, extension: str = "mp4") -> str:
    """Synchronous part: write to disk, upload to COS if enabled."""
    filename = f"{uuid.uuid4().hex}.{extension}"
    path = VIDEOS_DIR / filename
    path.write_bytes(data)

    if COS_ENABLED:
        from api.services.cos_client import upload_file
        url = upload_file(path, "videos", filename)
        path.unlink(missing_ok=True)
        return url

    return filename


async def save_video(data: bytes, extension: str = "mp4") -> str:
    """Save video, upload to COS if enabled, delete local copy.

    Returns COS URL (if enabled) or local filename.
    Runs COS upload in a thread to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_sync_save_video, data, extension)


def _sync_save_video_keep_local(data: bytes, extension: str = "mp4") -> tuple[str, str]:
    """Synchronous part: write to disk, upload to COS, keep local file."""
    filename = f"{uuid.uuid4().hex}.{extension}"
    path = VIDEOS_DIR / filename
    path.write_bytes(data)

    if COS_ENABLED:
        from api.services.cos_client import upload_file
        url = upload_file(path, "videos", filename)
        return filename, url

    return filename, filename


async def save_video_keep_local(data: bytes, extension: str = "mp4") -> tuple[str, str]:
    """Save video, upload to COS but keep local file (for chain frame extraction).

    Returns (filename, url_or_filename).
    Runs COS upload in a thread to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_sync_save_video_keep_local, data, extension)


async def save_upload(data: bytes, original_name: str) -> tuple[str, str]:
    """Save uploaded image. Returns (filename, url_or_filename).

    filename is always the local name (ComfyUI needs it).
    url_or_filename is the COS URL if enabled, otherwise the local API path.
    Runs COS upload in a thread to avoid blocking the event loop.
    """
    ext = Path(original_name).suffix or ".png"
    filename = f"{uuid.uuid4().hex}{ext}"
    path = UPLOADS_DIR / filename
    path.write_bytes(data)

    if COS_ENABLED:
        from api.services.cos_client import upload_file
        url = await asyncio.to_thread(upload_file, path, "uploads", filename)
        # Don't delete — ComfyUI may need the local file
        return filename, url

    # Return local API path when COS is not enabled
    return filename, f"/uploads/{filename}"


async def get_video_path(filename: str) -> Path | None:
    """Get local path for a video. Downloads from COS if not found locally."""
    path = VIDEOS_DIR / filename
    if path.exists():
        return path

    if COS_ENABLED:
        from api.services.cos_client import download_file
        try:
            await asyncio.to_thread(download_file, "videos", filename, path)
            return path
        except Exception as e:
            logger.warning("COS download failed for %s: %s", filename, e)

    return None


async def get_video_path_from_url(video_url: str) -> Path | None:
    """Extract filename from a video URL (COS or local) and return local path."""
    if not video_url:
        return None

    if COS_ENABLED:
        from api.services.cos_client import parse_cos_url
        parsed = parse_cos_url(video_url)
        if parsed:
            subdir, filename = parsed
            if subdir == "videos":
                return await get_video_path(filename)

    # Local URL format: /api/v1/results/{filename}
    filename = video_url.rsplit("/", 1)[-1]
    return await get_video_path(filename)


async def cleanup_video(filename: str):
    """Delete video from local disk and COS."""
    path = VIDEOS_DIR / filename
    if path.exists():
        path.unlink()

    if COS_ENABLED:
        from api.services.cos_client import delete_file
        try:
            await asyncio.to_thread(delete_file, "videos", filename)
        except Exception as e:
            logger.warning("COS delete failed for %s: %s", filename, e)


def cleanup_local(filename: str):
    """Delete only the local copy of a video (used by chain worker after concat)."""
    path = VIDEOS_DIR / filename
    if path.exists():
        path.unlink()
        logger.debug("Cleaned up local file: %s", filename)
