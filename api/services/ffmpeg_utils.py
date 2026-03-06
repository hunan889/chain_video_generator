import asyncio
import logging
import uuid
from pathlib import Path
from api.config import UPLOADS_DIR, VIDEOS_DIR

logger = logging.getLogger(__name__)


async def extract_first_frame(video_path: Path) -> Path:
    """Extract the first frame of a video as PNG using ffmpeg."""
    output = UPLOADS_DIR / f"{uuid.uuid4().hex}.png"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-frames:v", "1", "-q:v", "2", str(output),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not output.exists():
        raise RuntimeError(f"ffmpeg extract_first_frame failed: {stderr.decode()[-500:]}")
    logger.info("Extracted first frame: %s -> %s", video_path.name, output.name)
    return output


async def extract_last_frame(video_path: Path) -> Path:
    """Extract the last frame of a video as PNG using ffmpeg."""
    output = UPLOADS_DIR / f"{uuid.uuid4().hex}.png"
    cmd = [
        "ffmpeg", "-y", "-sseof", "-0.1", "-i", str(video_path),
        "-frames:v", "1", "-q:v", "2", str(output),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not output.exists():
        raise RuntimeError(f"ffmpeg extract_last_frame failed: {stderr.decode()[-500:]}")
    logger.info("Extracted last frame: %s -> %s", video_path.name, output.name)
    return output


async def extract_last_n_frames_video(video_path: Path, n_frames: int = 5, fps: int = 16) -> Path:
    """Extract the last N frames from a video as a short mp4 clip."""
    output = UPLOADS_DIR / f"last{n_frames}f_{uuid.uuid4().hex}.mp4"
    duration = n_frames / fps + 0.1  # small buffer
    cmd = [
        "ffmpeg", "-y", "-sseof", f"-{duration}",
        "-i", str(video_path),
        "-frames:v", str(n_frames),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "15",
        "-an", str(output),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not output.exists():
        raise RuntimeError(f"ffmpeg extract_last_n_frames failed: {stderr.decode()[-500:]}")
    logger.info("Extracted last %d frames: %s -> %s", n_frames, video_path.name, output.name)
    return output


async def concat_videos(video_paths: list[Path], fps: int = 24, transition: str = "none") -> Path:
    """Concatenate multiple videos. Supports 'none' (direct) or 'crossfade' transition."""
    if len(video_paths) == 1:
        return video_paths[0]

    if transition == "crossfade":
        return await _concat_crossfade(video_paths, fps)

    return await _concat_direct(video_paths, fps)


async def _concat_direct(video_paths: list[Path], fps: int) -> Path:
    """Concatenate videos using concat demuxer (no transition)."""
    concat_list = VIDEOS_DIR / f"concat_{uuid.uuid4().hex}.txt"
    output = VIDEOS_DIR / f"{uuid.uuid4().hex}.mp4"

    try:
        with open(concat_list, "w") as f:
            for p in video_paths:
                f.write(f"file '{p}'\n")

        # Try stream copy first (fast, lossless)
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list), "-c", "copy", str(output),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0 or not output.exists():
            logger.warning("concat -c copy failed, falling back to re-encode")
            output.unlink(missing_ok=True)
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-r", str(fps), "-pix_fmt", "yuv420p", str(output),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not output.exists():
                raise RuntimeError(f"ffmpeg concat re-encode failed: {stderr.decode()[-500:]}")

        logger.info("Concatenated %d videos -> %s", len(video_paths), output.name)
        return output
    finally:
        concat_list.unlink(missing_ok=True)


async def _concat_crossfade(video_paths: list[Path], fps: int, fade_duration: float = 0.5) -> Path:
    """Concatenate videos with crossfade transitions using xfade filter."""
    output = VIDEOS_DIR / f"{uuid.uuid4().hex}.mp4"
    n = len(video_paths)

    # Get durations of each video
    durations = []
    for p in video_paths:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(p),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        try:
            durations.append(float(stdout.decode().strip()))
        except ValueError:
            durations.append(3.0)

    # Build xfade filter chain
    # For N videos: N-1 xfade filters chained
    inputs = []
    for p in video_paths:
        inputs.extend(["-i", str(p)])

    if n == 2:
        offset = max(0, durations[0] - fade_duration)
        filter_str = f"[0:v][1:v]xfade=transition=fade:duration={fade_duration}:offset={offset},format=yuv420p"
    else:
        # Chain multiple xfades
        parts = []
        cumulative_offset = 0
        for i in range(n - 1):
            cumulative_offset += durations[i] - fade_duration
            if i == 0:
                parts.append(f"[0:v][1:v]xfade=transition=fade:duration={fade_duration}:offset={max(0, durations[0] - fade_duration)}[v{i}]")
            elif i < n - 2:
                parts.append(f"[v{i-1}][{i+1}:v]xfade=transition=fade:duration={fade_duration}:offset={max(0, cumulative_offset)}[v{i}]")
            else:
                parts.append(f"[v{i-1}][{i+1}:v]xfade=transition=fade:duration={fade_duration}:offset={max(0, cumulative_offset)},format=yuv420p")
        filter_str = ";".join(parts)

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_str,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-r", str(fps), str(output),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0 or not output.exists():
        logger.warning("crossfade failed (%s), falling back to direct concat", stderr.decode()[-200:])
        return await _concat_direct(video_paths, fps)

    logger.info("Crossfade concatenated %d videos -> %s", n, output.name)
    return output
