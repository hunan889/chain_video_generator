"""
Video first frame extraction service.

This module handles extracting the first frame from video URLs and caching them.
It checks if a first frame already exists, and if not, downloads the video,
extracts the frame using ffmpeg, and uploads it to COS.
"""
import asyncio
import logging
import hashlib
import aiohttp
from pathlib import Path
from typing import Optional
from api.config import UPLOADS_DIR, COS_ENABLED

logger = logging.getLogger(__name__)


def _get_frame_filename_from_video_url(video_url: str) -> str:
    """
    Generate a consistent first frame filename from a video URL.

    Strategy:
    - Use MD5 hash of the video URL to generate a unique, consistent filename
    - This ensures the same video URL always maps to the same frame filename
    - Format: frame_{hash}.png

    Args:
        video_url: The video URL

    Returns:
        Frame filename (e.g., "frame_abc123def456.png")
    """
    url_hash = hashlib.md5(video_url.encode('utf-8')).hexdigest()[:16]
    return f"frame_{url_hash}.png"


def _get_frame_url_from_video_url(video_url: str) -> str:
    """
    Generate the expected first frame URL from a video URL.

    Strategy:
    - Replace /video/ with /frames/ in the path
    - Replace video extension with .png
    - Keep the same base filename

    Examples:
    - http://cdn.imagime.co/bot/cache/video/abc.mp4
      -> http://cdn.imagime.co/bot/cache/frames/abc.png
    - https://cdn.imagime.co/bot/cache/video/xyz.mp4
      -> https://cdn.imagime.co/bot/cache/frames/xyz.png

    Args:
        video_url: The video URL

    Returns:
        Expected frame URL
    """
    if not video_url:
        return ""

    # Replace /video/ with /frames/
    frame_url = video_url.replace('/video/', '/frames/')

    # Replace video extension with .png
    video_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.flv', '.webm')
    for ext in video_extensions:
        if frame_url.lower().endswith(ext):
            frame_url = frame_url[:-len(ext)] + '.png'
            break

    return frame_url


async def _check_frame_exists(frame_url: str) -> bool:
    """
    Check if a first frame already exists at the given URL.

    Args:
        frame_url: The frame URL to check

    Returns:
        True if frame exists (HTTP 200), False otherwise
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(frame_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                exists = resp.status == 200
                logger.info(f"Frame existence check: {frame_url} -> {exists} (status: {resp.status})")
                return exists
    except Exception as e:
        logger.warning(f"Failed to check frame existence for {frame_url}: {e}")
        return False


async def _download_video(video_url: str, local_path: Path) -> None:
    """
    Download video from URL to local path.

    Args:
        video_url: The video URL
        local_path: Local path to save the video
    """
    logger.info(f"Downloading video: {video_url}")
    async with aiohttp.ClientSession() as session:
        async with session.get(video_url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to download video: HTTP {resp.status}")

            video_data = await resp.read()
            local_path.write_bytes(video_data)
            logger.info(f"Downloaded video to {local_path} ({len(video_data)} bytes)")


async def _extract_first_frame(video_path: Path, frame_path: Path, width: int, height: int) -> None:
    """
    Extract first frame from video using ffmpeg.

    Args:
        video_path: Path to the video file
        frame_path: Path to save the extracted frame
        width: Target width
        height: Target height
    """
    logger.info(f"Extracting first frame from {video_path} to {frame_path} ({width}x{height})")

    # ffmpeg command to extract first frame and resize
    cmd = [
        'ffmpeg',
        '-i', str(video_path),
        '-vframes', '1',  # Extract only 1 frame
        '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2',
        '-f', 'image2',
        '-y',  # Overwrite output file
        str(frame_path)
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode('utf-8', errors='ignore')
        raise Exception(f"ffmpeg failed with code {process.returncode}: {error_msg}")

    if not frame_path.exists():
        raise Exception(f"Frame extraction failed: {frame_path} not created")

    logger.info(f"Successfully extracted first frame to {frame_path}")


async def _upload_frame_to_cos(frame_path: Path, frame_filename: str) -> str:
    """
    Upload extracted frame to COS or save locally.

    Args:
        frame_path: Local path of the frame
        frame_filename: Filename to use in COS or local storage

    Returns:
        COS URL or local URL of the uploaded frame
    """
    if COS_ENABLED:
        from api.services.cos_client import upload_file

        # Upload to "frames" subdirectory
        url = await asyncio.to_thread(upload_file, frame_path, "frames", frame_filename)
        logger.info(f"Uploaded frame to COS: {url}")
        return url
    else:
        # COS not enabled, save to local uploads directory
        local_frame_path = UPLOADS_DIR / frame_filename

        # Copy frame to uploads directory if not already there
        if frame_path != local_frame_path:
            import shutil
            shutil.copy2(frame_path, local_frame_path)

        # Return local URL
        local_url = f"/api/v1/results/{frame_filename}"
        logger.info(f"Saved frame locally: {local_url}")
        return local_url


async def get_video_first_frame(video_url: str, width: int = 1280, height: int = 720) -> str:
    """
    Get the first frame URL for a video. If the frame doesn't exist, extract and upload it.

    Workflow:
    1. Generate expected frame URL from video URL
    2. Check if frame already exists at that URL
    3. If exists, return the frame URL
    4. If not exists:
       a. Download the video
       b. Extract first frame using ffmpeg
       c. Upload frame to COS at the expected location
       d. Return the frame URL

    Args:
        video_url: The video URL
        width: Target width for the frame (default: 1280)
        height: Target height for the frame (default: 720)

    Returns:
        URL of the first frame image

    Raises:
        Exception: If video download, frame extraction, or upload fails
    """
    try:
        # Step 1: Generate expected frame URL
        frame_url = _get_frame_url_from_video_url(video_url)
        logger.info(f"Expected frame URL: {frame_url}")

        # Step 2: Check if frame already exists
        if await _check_frame_exists(frame_url):
            logger.info(f"Frame already exists: {frame_url}")
            return frame_url

        # Step 3: Frame doesn't exist, need to extract and upload
        logger.info(f"Frame doesn't exist, extracting from video: {video_url}")

        # Generate local paths
        frame_filename = _get_frame_filename_from_video_url(video_url)
        video_filename = f"temp_video_{frame_filename[6:-4]}.mp4"  # Remove "frame_" prefix and ".png" suffix

        video_path = UPLOADS_DIR / video_filename
        frame_path = UPLOADS_DIR / frame_filename

        try:
            # Step 3a: Download video
            await _download_video(video_url, video_path)

            # Step 3b: Extract first frame
            await _extract_first_frame(video_path, frame_path, width, height)

            # Step 3c: Upload frame to COS
            frame_url = await _upload_frame_to_cos(frame_path, frame_filename)

            logger.info(f"Successfully created and uploaded frame: {frame_url}")
            return frame_url

        finally:
            # Cleanup temporary files
            if video_path.exists():
                video_path.unlink()
                logger.debug(f"Cleaned up temporary video: {video_path}")
            # Keep frame in uploads directory if COS is not enabled
            if COS_ENABLED and frame_path.exists():
                frame_path.unlink()
                logger.debug(f"Cleaned up temporary frame: {frame_path}")

    except Exception as e:
        logger.error(f"Failed to get video first frame for {video_url}: {e}", exc_info=True)
        raise


async def convert_video_url_to_frame(video_url: str, width: int = 1280, height: int = 720) -> str:
    """
    Convert a video URL to its first frame URL. If the URL is not a video, return as-is.

    This is a convenience wrapper around get_video_first_frame that:
    - Checks if the URL is actually a video
    - Returns the URL unchanged if it's not a video
    - Calls get_video_first_frame if it is a video

    Args:
        video_url: The URL to convert
        width: Target width for the frame (default: 1280)
        height: Target height for the frame (default: 720)

    Returns:
        Frame URL if input was a video, otherwise the original URL
    """
    if not video_url:
        return video_url

    # Check if it's a video file
    video_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.flv', '.webm')
    is_video = any(video_url.lower().endswith(ext) for ext in video_extensions)

    if not is_video:
        logger.debug(f"URL is not a video, returning as-is: {video_url}")
        return video_url

    # It's a video, get the first frame
    return await get_video_first_frame(video_url, width, height)
