"""Unit tests for parent_video_extractor image-URL guard.

Ensures that ClothOff image results (eraser/undress/face_swap_photo) are
rejected early — before any ffmpeg subprocess is spawned — when passed as
a parent video URL.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from api_gateway.services.parent_video_extractor import (
    _is_image_url,
    extract_parent_video_anchors,
    clear_cache,
)


# ---------------------------------------------------------------------------
# _is_image_url helper
# ---------------------------------------------------------------------------

class TestIsImageUrl:

    def test_png_detected(self):
        assert _is_image_url("https://example.com/result.png") is True

    def test_jpg_detected(self):
        assert _is_image_url("https://example.com/photo.jpg") is True

    def test_jpeg_detected(self):
        assert _is_image_url("https://example.com/photo.jpeg") is True

    def test_webp_detected(self):
        assert _is_image_url("https://example.com/img.webp") is True

    def test_mp4_not_image(self):
        assert _is_image_url("https://example.com/video.mp4") is False

    def test_query_string_ignored(self):
        """Extension check strips query params first."""
        assert _is_image_url("https://cos.example/output.png?X-Amz-Expires=3600") is True

    def test_mp4_with_query_string(self):
        assert _is_image_url("https://cos.example/video.mp4?token=abc") is False

    def test_case_insensitive(self):
        assert _is_image_url("https://example.com/PHOTO.PNG") is True


# ---------------------------------------------------------------------------
# extract_parent_video_anchors — image URL early-return guard
# ---------------------------------------------------------------------------

class TestImageUrlGuard:

    def setup_method(self):
        clear_cache()

    @pytest.mark.asyncio
    async def test_empty_url_returns_empty(self):
        cos = AsyncMock()
        result = await extract_parent_video_anchors("", cos_client=cos)
        assert result == ("", "")

    @pytest.mark.asyncio
    async def test_png_url_returns_empty_without_ffmpeg(self):
        """PNG parent URL should return ('', '') and never touch ffmpeg."""
        cos = AsyncMock()
        with patch("api_gateway.services.parent_video_extractor._download_video") as dl:
            result = await extract_parent_video_anchors(
                "https://cos.example/face_swap_result.png",
                cos_client=cos,
            )

        assert result == ("", "")
        dl.assert_not_called()

    @pytest.mark.asyncio
    async def test_jpg_url_returns_empty_without_ffmpeg(self):
        cos = AsyncMock()
        with patch("api_gateway.services.parent_video_extractor._download_video") as dl:
            result = await extract_parent_video_anchors(
                "https://cos.example/undress_result.jpg?X-Amz-Signature=xyz",
                cos_client=cos,
            )

        assert result == ("", "")
        dl.assert_not_called()

    @pytest.mark.asyncio
    async def test_video_url_proceeds_to_download(self):
        """mp4 URL must NOT be filtered; download should be attempted."""
        cos = AsyncMock()
        with patch(
            "api_gateway.services.parent_video_extractor._download_video",
            side_effect=RuntimeError("intentional stop"),
        ) as dl:
            result = await extract_parent_video_anchors(
                "https://cos.example/animation.mp4",
                cos_client=cos,
            )

        # Download was called (then intentionally raised, returning partial result)
        dl.assert_called_once()
        # Result is partial ("", "") because everything failed after download raised
        assert result == ("", "")
