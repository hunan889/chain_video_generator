"""Unit tests for api_gateway.services.ffmpeg_utils.

These tests generate a tiny synthetic video on the fly so they don't depend
on any fixtures, and they're skipped automatically if ffmpeg is not on the
PATH (e.g. CI containers without it).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from api_gateway.services.ffmpeg_utils import (
    extract_first_frame,
    extract_last_n_frames_video,
)

ffmpeg_available = shutil.which("ffmpeg") is not None
pytestmark = pytest.mark.skipif(
    not ffmpeg_available, reason="ffmpeg not installed in test environment",
)


def _make_test_video(path: Path, *, duration: int = 2, fps: int = 16) -> None:
    """Generate a small solid-color test video using ffmpeg."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=red:s=64x64:d={duration}:r={fps}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_extract_first_frame_returns_valid_png():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video = tmp / "src.mp4"
        _make_test_video(video)

        out = await extract_first_frame(video, tmp)

        assert out.exists()
        assert out.suffix == ".png"
        assert out.stat().st_size > 0
        # PNG magic header
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_extract_last_n_frames_video_returns_mp4():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video = tmp / "src.mp4"
        _make_test_video(video, duration=2, fps=16)

        out = await extract_last_n_frames_video(video, tmp, n_frames=10, fps=16)

        assert out.exists()
        assert out.suffix == ".mp4"
        assert out.stat().st_size > 0

        # Probe frame count with ffprobe
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-count_frames",
                "-show_entries", "stream=nb_read_frames",
                "-of", "default=nokey=1:noprint_wrappers=1",
                str(out),
            ],
            capture_output=True, text=True, check=True,
        )
        nb_frames = int(probe.stdout.strip())
        # Should be at most n_frames (and at least 1 — short videos may yield fewer)
        assert 1 <= nb_frames <= 10


@pytest.mark.asyncio
async def test_extract_first_frame_raises_on_bad_input():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        bogus = tmp / "not_a_video.txt"
        bogus.write_text("hello")

        with pytest.raises(RuntimeError, match="extract_first_frame failed"):
            await extract_first_frame(bogus, tmp)


@pytest.mark.asyncio
async def test_extract_first_frame_creates_output_dir_if_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video = tmp / "src.mp4"
        _make_test_video(video)

        nested = tmp / "nested" / "more"
        out = await extract_first_frame(video, nested)
        assert out.exists()
        assert nested.is_dir()
