"""Unit tests for api_gateway.services.parent_video_extractor.

Mocks aiohttp + ffmpeg + COS so the test runs in isolation without network
access or a working ffmpeg install.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_gateway.services import parent_video_extractor as pve


@pytest.fixture(autouse=True)
def _clear_cache():
    pve.clear_cache()
    yield
    pve.clear_cache()


def _make_cos_client_mock(*, first_url: str = "https://cdn.test/frames/x_origin_first.png",
                         video_url: str = "https://cdn.test/videos/x_motion10.mp4") -> MagicMock:
    mock = MagicMock()
    # Return different URLs depending on which subdir is requested.
    def _upload(local_path, subdir, filename):
        if subdir == "frames":
            return first_url
        if subdir == "videos":
            return video_url
        return f"https://cdn.test/{subdir}/{filename}"
    mock.upload_file.side_effect = _upload
    return mock


@pytest.mark.asyncio
async def test_extract_returns_both_anchors_on_happy_path():
    cos = _make_cos_client_mock()

    async def fake_download(url, dest):
        Path(dest).write_bytes(b"fake mp4 bytes")

    async def fake_first_frame(video_path, output_dir):
        out = Path(output_dir) / "first.png"
        out.write_bytes(b"\x89PNG\r\n\x1a\n")
        return out

    async def fake_motion(video_path, output_dir, n_frames=10, fps=16):
        out = Path(output_dir) / "motion.mp4"
        out.write_bytes(b"fake mp4 motion")
        return out

    with patch.object(pve, "_download_video", side_effect=fake_download), \
         patch.object(pve, "extract_first_frame", side_effect=fake_first_frame), \
         patch.object(pve, "extract_last_n_frames_video", side_effect=fake_motion):
        origin, prev = await pve.extract_parent_video_anchors(
            "https://cdn.test/cvid/videos/parent.mp4",
            cos_client=cos,
            motion_frames=10,
            fps=16,
        )

    assert origin == "https://cdn.test/frames/x_origin_first.png"
    assert prev == "https://cdn.test/videos/x_motion10.mp4"
    assert cos.upload_file.call_count == 2


@pytest.mark.asyncio
async def test_extract_always_uses_first_frame_for_anchor():
    """The origin anchor must come from the parent video's FIRST frame
    (where the character is established), never from a last-frame URL.
    Earlier versions had a ``prefer_lossless_frame_url`` shortcut that
    fed the lossless LAST frame in here by mistake; that's gone now."""
    cos = _make_cos_client_mock()

    async def fake_download(url, dest):
        Path(dest).write_bytes(b"fake mp4")

    first_frame_called = False

    async def fake_first_frame(video_path, output_dir):
        nonlocal first_frame_called
        first_frame_called = True
        out = Path(output_dir) / "first.png"
        out.write_bytes(b"\x89PNG")
        return out

    async def fake_motion(video_path, output_dir, n_frames=10, fps=16):
        out = Path(output_dir) / "motion.mp4"
        out.write_bytes(b"motion")
        return out

    with patch.object(pve, "_download_video", side_effect=fake_download), \
         patch.object(pve, "extract_first_frame", side_effect=fake_first_frame), \
         patch.object(pve, "extract_last_n_frames_video", side_effect=fake_motion):
        origin, prev = await pve.extract_parent_video_anchors(
            "https://cdn.test/cvid/videos/parent.mp4",
            cos_client=cos,
            motion_frames=10,
        )

    assert first_frame_called, "extract_first_frame should always be called"
    assert origin == "https://cdn.test/frames/x_origin_first.png"
    assert prev == "https://cdn.test/videos/x_motion10.mp4"


@pytest.mark.asyncio
async def test_extract_caches_result():
    cos = _make_cos_client_mock()

    download_mock = AsyncMock()
    first_frame_mock = AsyncMock()
    motion_mock = AsyncMock()

    async def fake_download(url, dest):
        Path(dest).write_bytes(b"fake")
    download_mock.side_effect = fake_download

    async def fake_first_frame(video_path, output_dir):
        out = Path(output_dir) / "first.png"
        out.write_bytes(b"PNG")
        return out
    first_frame_mock.side_effect = fake_first_frame

    async def fake_motion(video_path, output_dir, n_frames=10, fps=16):
        out = Path(output_dir) / "motion.mp4"
        out.write_bytes(b"mp4")
        return out
    motion_mock.side_effect = fake_motion

    with patch.object(pve, "_download_video", download_mock), \
         patch.object(pve, "extract_first_frame", first_frame_mock), \
         patch.object(pve, "extract_last_n_frames_video", motion_mock):

        # First call — actually extracts
        await pve.extract_parent_video_anchors(
            "https://cdn.test/parent.mp4", cos_client=cos, motion_frames=10,
        )
        # Second call — should hit cache, no new download/extract
        await pve.extract_parent_video_anchors(
            "https://cdn.test/parent.mp4", cos_client=cos, motion_frames=10,
        )

    assert download_mock.call_count == 1
    assert first_frame_mock.call_count == 1
    assert motion_mock.call_count == 1


@pytest.mark.asyncio
async def test_cache_key_includes_motion_frames():
    """Different motion_frames should bust the cache."""
    cos = _make_cos_client_mock()
    download_mock = AsyncMock()

    async def fake_download(url, dest):
        Path(dest).write_bytes(b"fake")
    download_mock.side_effect = fake_download

    async def fake_first_frame(video_path, output_dir):
        out = Path(output_dir) / "first.png"
        out.write_bytes(b"PNG")
        return out

    async def fake_motion(video_path, output_dir, n_frames=10, fps=16):
        out = Path(output_dir) / "motion.mp4"
        out.write_bytes(b"mp4")
        return out

    with patch.object(pve, "_download_video", download_mock), \
         patch.object(pve, "extract_first_frame", side_effect=fake_first_frame), \
         patch.object(pve, "extract_last_n_frames_video", side_effect=fake_motion):
        await pve.extract_parent_video_anchors(
            "https://cdn.test/p.mp4", cos_client=cos, motion_frames=10,
        )
        await pve.extract_parent_video_anchors(
            "https://cdn.test/p.mp4", cos_client=cos, motion_frames=20,
        )

    assert download_mock.call_count == 2  # both motion_frame values trigger fresh download


@pytest.mark.asyncio
async def test_empty_video_url_returns_empty_tuple():
    cos = _make_cos_client_mock()
    origin, prev = await pve.extract_parent_video_anchors("", cos_client=cos)
    assert origin == ""
    assert prev == ""
    cos.upload_file.assert_not_called()


@pytest.mark.asyncio
async def test_partial_failure_returns_partial_result():
    """If first-frame extraction fails but motion extraction succeeds, we
    still return the motion URL (and vice versa)."""
    cos = _make_cos_client_mock()

    async def fake_download(url, dest):
        Path(dest).write_bytes(b"fake")

    async def failing_first_frame(video_path, output_dir):
        raise RuntimeError("ffmpeg said no")

    async def fake_motion(video_path, output_dir, n_frames=10, fps=16):
        out = Path(output_dir) / "motion.mp4"
        out.write_bytes(b"mp4")
        return out

    with patch.object(pve, "_download_video", side_effect=fake_download), \
         patch.object(pve, "extract_first_frame", side_effect=failing_first_frame), \
         patch.object(pve, "extract_last_n_frames_video", side_effect=fake_motion):
        origin, prev = await pve.extract_parent_video_anchors(
            "https://cdn.test/p.mp4", cos_client=cos,
        )

    assert origin == ""  # extraction failed
    assert prev == "https://cdn.test/videos/x_motion10.mp4"  # but motion succeeded
