"""High-level helper: turn a parent video URL into continuation anchors.

Given the final video URL of a parent workflow, this module downloads the
video to a temp dir, runs ffmpeg to produce:

1. The **first frame** as a PNG — used as the CLIPVision identity anchor
   (``PainterLongVideo.initial_reference_image``). The first frame is
   chosen because that's where the character is established in the parent
   video; using the last frame would bypass the whole point of having a
   separate anchor (since the last frame is already used as the new
   segment's start image).
2. A small mp4 containing the last ``motion_frames`` frames — used as the
   multi-frame motion reference (``PainterLongVideo.previous_video``).

Both artifacts are uploaded to COS and returned as public URLs that the
GPU worker can later download via the ``input_files`` placeholder system.

Results are cached in-process by ``md5(video_url) + motion_frames`` to
avoid re-downloading and re-extracting when the same parent is reused
(e.g. multiple continuations or retries). The cache is a small bounded
dict; unbounded growth isn't a concern because the key is per-video and
the workflow engine runs under a single uvicorn worker today.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import aiohttp

from api_gateway.services.ffmpeg_utils import (
    extract_first_frame,
    extract_last_n_frames_video,
)

if TYPE_CHECKING:
    from shared.cos.client import COSClient

logger = logging.getLogger(__name__)

# In-process cache: {(md5(video_url), motion_frames): (origin_first_frame_url, prev_video_url)}
# Bounded to avoid unbounded growth on long-running processes.
_CACHE: dict[tuple[str, int], tuple[str, str]] = {}
_CACHE_MAX = 128

# Max parent video size we'll download (safety cap; a 5s 720p video is ~3MB).
_MAX_VIDEO_BYTES = 200 * 1024 * 1024  # 200 MB


def _cache_key(video_url: str, motion_frames: int) -> tuple[str, int]:
    digest = hashlib.md5(video_url.encode("utf-8")).hexdigest()
    return (digest, motion_frames)


def _cache_put(key: tuple[str, int], value: tuple[str, str]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        # Drop oldest (dicts preserve insertion order in 3.7+)
        try:
            oldest = next(iter(_CACHE))
            _CACHE.pop(oldest, None)
        except StopIteration:
            pass
    _CACHE[key] = value


async def _download_video(url: str, dest: Path) -> None:
    """Download *url* to *dest*. Raises RuntimeError on failure."""
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"parent video download failed: HTTP {resp.status} from {url[:120]}"
                )
            total = 0
            with open(dest, "wb") as fh:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > _MAX_VIDEO_BYTES:
                        raise RuntimeError(
                            f"parent video exceeds {_MAX_VIDEO_BYTES} byte cap"
                        )
                    fh.write(chunk)
    logger.info("Downloaded parent video (%d bytes) -> %s", dest.stat().st_size, dest.name)


def _is_image_url(url: str) -> bool:
    """Cheap check: URL path ends in an image extension."""
    lowered = url.split("?", 1)[0].lower()
    return lowered.endswith((".png", ".jpg", ".jpeg", ".webp"))


async def extract_parent_video_anchors(
    video_url: str,
    *,
    cos_client: "COSClient",
    motion_frames: int = 10,
    fps: int = 16,
) -> tuple[str, str]:
    """Produce (origin_first_frame_url, prev_video_url) for a continuation.

    Args:
        video_url: Public URL of the parent chain's final video.
        cos_client: Injected COS client used for uploads (sync API, wrapped
            in ``asyncio.to_thread``).
        motion_frames: Number of trailing parent frames to bundle as the
            PainterLongVideo ``previous_video`` input.
        fps: Parent video frame rate (16 for all chain-generated videos).

    Returns:
        A 2-tuple ``(origin_first_frame_url, prev_video_url)``. Either may
        be an empty string on extraction failure — the caller is expected
        to fall back gracefully (e.g. continuation still works with just
        the last-frame start image).
    """
    if not video_url:
        return ("", "")

    cache_key = _cache_key(video_url, motion_frames)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        logger.info("parent_video_extractor: cache hit for %s...", video_url[:80])
        return cached

    logger.info(
        "parent_video_extractor: extracting anchors from %s (motion_frames=%d)",
        video_url[:120], motion_frames,
    )

    origin_first_frame_url = ""
    prev_video_url = ""

    try:
        with tempfile.TemporaryDirectory(prefix="parent_anchor_") as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            video_path = tmpdir / "parent.mp4"

            await _download_video(video_url, video_path)

            # --- Origin first frame: ALWAYS extract from the parent video.
            # The character / scene is established in the opening frame, so
            # using it as the CLIPVision identity anchor maximises consistency.
            # Note: do NOT shortcut to parent.lossless_last_frame_url here —
            # that's the LAST frame (used as the new segment's start image),
            # which would defeat the purpose of having a separate anchor.
            try:
                first_png = await extract_first_frame(video_path, tmpdir)
                digest = hashlib.md5(video_url.encode("utf-8")).hexdigest()
                origin_first_frame_url = await asyncio.to_thread(
                    cos_client.upload_file,
                    first_png,
                    "frames",
                    f"{digest}_origin_first.png",
                )
                logger.info(
                    "parent_video_extractor: origin first frame uploaded -> %s",
                    origin_first_frame_url,
                )
            except Exception as exc:
                logger.warning(
                    "parent_video_extractor: first-frame extraction failed: %s", exc,
                )

            # --- Motion reference mp4 ---
            try:
                motion_mp4 = await extract_last_n_frames_video(
                    video_path, tmpdir, n_frames=motion_frames, fps=fps,
                )
                digest = hashlib.md5(video_url.encode("utf-8")).hexdigest()
                prev_video_url = await asyncio.to_thread(
                    cos_client.upload_file,
                    motion_mp4,
                    "videos",
                    f"{digest}_motion{motion_frames}.mp4",
                )
                logger.info(
                    "parent_video_extractor: motion reference (%d frames) uploaded -> %s",
                    motion_frames, prev_video_url,
                )
            except Exception as exc:
                logger.warning(
                    "parent_video_extractor: motion-reference extraction failed: %s", exc,
                )
    except Exception as exc:
        logger.warning(
            "parent_video_extractor: unexpected error, returning partial result: %s", exc,
        )

    result = (origin_first_frame_url, prev_video_url)
    # Cache even partial results so we don't retry forever on a broken parent
    _cache_put(cache_key, result)
    return result


def clear_cache() -> None:
    """Test helper: drop all cached anchor URLs."""
    _CACHE.clear()
