"""Gateway-side ffmpeg helpers for parent-video anchor extraction.

Ported from ``api/services/ffmpeg_utils.py`` (legacy monolith) but without
any global UPLOADS_DIR dependency -- the caller supplies the output dir so
these helpers are trivially unit-testable with ``tempfile.TemporaryDirectory``.

Only the two helpers used by the continuation flow are ported here:

- :func:`extract_first_frame` — 1-frame PNG for CLIPVision identity anchor
- :func:`extract_last_n_frames_video` — small mp4 for PainterLongVideo motion ref

Both are async subprocess wrappers around ``ffmpeg`` and raise
``RuntimeError`` on non-zero exit with the tail of stderr included.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


async def extract_first_frame(video_path: Path, output_dir: Path) -> Path:
    """Extract the first frame of *video_path* as a PNG.

    Args:
        video_path: Path to the source video file.
        output_dir: Directory where the PNG will be written. Must exist.

    Returns:
        Path to the written PNG file (unique filename inside ``output_dir``).

    Raises:
        RuntimeError: If ffmpeg exits with a non-zero status or the output
            file is missing / empty.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"first_{uuid.uuid4().hex}.png"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(output),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        tail = stderr.decode(errors="replace")[-500:] if stderr else ""
        raise RuntimeError(f"ffmpeg extract_first_frame failed: {tail}")

    logger.info(
        "Extracted first frame: %s -> %s (%d bytes)",
        video_path.name, output.name, output.stat().st_size,
    )
    return output


async def extract_last_n_frames_video(
    video_path: Path,
    output_dir: Path,
    n_frames: int = 10,
    fps: int = 16,
) -> Path:
    """Extract the trailing *n_frames* frames of *video_path* into a short mp4.

    Used by the continuation flow to feed PainterLongVideo's ``previous_video``
    input with real motion rather than a single still frame. The clip is
    re-encoded with ``-crf 15`` so the resulting file is small while retaining
    enough fidelity for the downstream VAE encode.

    Args:
        video_path: Path to the source video file.
        output_dir: Directory where the mp4 will be written. Must exist.
        n_frames: Number of trailing frames to keep. PainterLongVideo caps at
            73 internally, but values beyond ~20 provide diminishing returns
            (see ``comfyui_nodes/PainterLongVideo/nodes.py``).
        fps: Source video frame rate (used to compute the ``-sseof`` offset).
            Chain-generated videos are always 16 fps today.

    Returns:
        Path to the written mp4 file (unique filename inside ``output_dir``).

    Raises:
        RuntimeError: If ffmpeg exits with a non-zero status or the output
            file is missing / empty.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"last{n_frames}f_{uuid.uuid4().hex}.mp4"

    # Seek to (duration - n_frames/fps - buffer) from the end of the file.
    duration = n_frames / max(fps, 1) + 0.1
    cmd = [
        "ffmpeg", "-y",
        "-sseof", f"-{duration}",
        "-i", str(video_path),
        "-frames:v", str(n_frames),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "15",
        "-an",
        str(output),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        tail = stderr.decode(errors="replace")[-500:] if stderr else ""
        raise RuntimeError(f"ffmpeg extract_last_n_frames_video failed: {tail}")

    logger.info(
        "Extracted last %d frames: %s -> %s (%d bytes)",
        n_frames, video_path.name, output.name, output.stat().st_size,
    )
    return output
