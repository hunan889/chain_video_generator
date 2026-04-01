"""Tests for Stage 2 -- First Frame Acquisition.

Uses fakeredis and mocked external services.
"""

import base64
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from api_gateway.services.stages.first_frame import (
    FirstFrameResult,
    _download_image_as_b64,
    _find_base_image,
    _upload_bytes_to_cos,
    acquire_first_frame,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_config():
    """Minimal GatewayConfig-like object."""
    cfg = MagicMock()
    cfg.cos_prefix = "wan22"
    cfg.cos_cdn_domain = "cdn.example.com"
    return cfg


@pytest.fixture
def mock_cos_client():
    """Mocked COSClient that stores uploads in memory."""
    client = MagicMock()
    uploaded = {}

    def fake_upload(local_path, subdir, filename):
        with open(local_path, "rb") as f:
            uploaded[(subdir, filename)] = f.read()
        return f"https://cdn.example.com/{subdir}/{filename}"

    client.upload_file = MagicMock(side_effect=fake_upload)
    client._uploaded = uploaded
    return client


@pytest.fixture
def mock_reactor_client():
    """Mocked ReactorClient."""
    client = MagicMock()
    # Return fake swapped bytes
    client.swap_face = AsyncMock(return_value=b"\x89PNG_SWAPPED_FACE")
    return client


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


class TestFindBaseImage:
    """Tests for _find_base_image (pure function)."""

    def test_pose_reference_has_priority(self):
        analysis = {"reference_image": "https://cdn.example.com/pose.png"}
        result = _find_base_image("face_reference", analysis, "https://user-ref.png")
        assert result == "https://cdn.example.com/pose.png"

    def test_fallback_to_user_reference(self):
        result = _find_base_image("face_reference", {}, "https://user-ref.png")
        assert result == "https://user-ref.png"

    def test_t2v_returns_none(self):
        result = _find_base_image("t2v", {}, "https://user-ref.png")
        assert result is None

    def test_no_match_returns_none(self):
        result = _find_base_image("face_reference", {}, None)
        assert result is None


class TestUploadBytesToCos:
    """Tests for _upload_bytes_to_cos."""

    def test_uploads_and_cleans_up(self, mock_cos_client):
        data = b"test image data"
        url = _upload_bytes_to_cos(data, mock_cos_client, "frames", "test.png")
        assert url == "https://cdn.example.com/frames/test.png"
        assert mock_cos_client._uploaded[("frames", "test.png")] == data


# ---------------------------------------------------------------------------
# acquire_first_frame tests
# ---------------------------------------------------------------------------


class TestAcquireFirstFrame:
    """Tests for the main acquire_first_frame function."""

    @pytest.mark.asyncio
    async def test_t2v_returns_none(self, mock_config, mock_cos_client, mock_reactor_client):
        result = await acquire_first_frame(
            workflow_id="wf_t2v",
            mode="t2v",
            uploaded_first_frame=None,
            reference_image=None,
            analysis_result={},
            face_swap_config={},
            is_continuation=False,
            parent_workflow=None,
            config=mock_config,
            redis=MagicMock(),
            cos_client=mock_cos_client,
            reactor_client=mock_reactor_client,
        )
        assert result.url is None
        assert result.source == "t2v"

    @pytest.mark.asyncio
    async def test_t2v_with_pose_returns_pose_url(self, mock_config, mock_cos_client, mock_reactor_client):
        result = await acquire_first_frame(
            workflow_id="wf_t2v_pose",
            mode="t2v",
            uploaded_first_frame=None,
            reference_image=None,
            analysis_result={"reference_image": "https://cdn.example.com/pose.png"},
            face_swap_config={},
            is_continuation=False,
            parent_workflow=None,
            config=mock_config,
            redis=MagicMock(),
            cos_client=mock_cos_client,
            reactor_client=mock_reactor_client,
        )
        assert result.url == "https://cdn.example.com/pose.png"
        assert result.source == "pose_reference"

    @pytest.mark.asyncio
    async def test_continuation_uses_lossless_frame(self, mock_config, mock_cos_client, mock_reactor_client):
        parent = {
            "lossless_last_frame_url": "https://cdn.example.com/lossless.png",
            "last_frame_url": "https://cdn.example.com/last.png",
        }
        result = await acquire_first_frame(
            workflow_id="wf_cont",
            mode="t2v",
            uploaded_first_frame=None,
            reference_image=None,
            analysis_result={},
            face_swap_config={},
            is_continuation=True,
            parent_workflow=parent,
            config=mock_config,
            redis=MagicMock(),
            cos_client=mock_cos_client,
            reactor_client=mock_reactor_client,
        )
        assert result.url == "https://cdn.example.com/lossless.png"
        assert result.source == "lossless_png"

    @pytest.mark.asyncio
    async def test_continuation_falls_back_to_last_frame(self, mock_config, mock_cos_client, mock_reactor_client):
        parent = {
            "last_frame_url": "https://cdn.example.com/last.png",
        }
        result = await acquire_first_frame(
            workflow_id="wf_cont_fb",
            mode="t2v",
            uploaded_first_frame=None,
            reference_image=None,
            analysis_result={},
            face_swap_config={},
            is_continuation=True,
            parent_workflow=parent,
            config=mock_config,
            redis=MagicMock(),
            cos_client=mock_cos_client,
            reactor_client=mock_reactor_client,
        )
        assert result.url == "https://cdn.example.com/last.png"
        assert result.source == "h264_extraction"

    @pytest.mark.asyncio
    async def test_continuation_with_face_swap(self, mock_config, mock_cos_client, mock_reactor_client):
        parent = {
            "lossless_last_frame_url": "https://cdn.example.com/lossless.png",
        }

        # Mock the image download for face swap
        with patch(
            "api_gateway.services.stages.first_frame._download_image_as_b64",
            new=AsyncMock(return_value=base64.b64encode(b"fake").decode()),
        ):
            result = await acquire_first_frame(
                workflow_id="wf_cont_fs",
                mode="t2v",
                uploaded_first_frame=None,
                reference_image="https://cdn.example.com/ref.png",
                analysis_result={},
                face_swap_config={"enabled": True, "strength": 0.9},
                is_continuation=True,
                parent_workflow=parent,
                config=mock_config,
                redis=MagicMock(),
                cos_client=mock_cos_client,
                reactor_client=mock_reactor_client,
            )
        assert result.face_swapped is True
        assert "face_swap" in result.source

    @pytest.mark.asyncio
    async def test_first_frame_mode_with_base64(self, mock_config, mock_cos_client, mock_reactor_client):
        # Create a base64 data URI
        img_bytes = b"\x89PNG_FAKE_IMAGE_DATA"
        img_b64 = base64.b64encode(img_bytes).decode()
        data_uri = f"data:image/png;base64,{img_b64}"

        result = await acquire_first_frame(
            workflow_id="wf_ff_b64",
            mode="first_frame",
            uploaded_first_frame=data_uri,
            reference_image=None,
            analysis_result={},
            face_swap_config={},
            is_continuation=False,
            parent_workflow=None,
            config=mock_config,
            redis=MagicMock(),
            cos_client=mock_cos_client,
            reactor_client=mock_reactor_client,
        )
        assert result.url is not None
        assert result.source == "upload"
        assert mock_cos_client.upload_file.called

    @pytest.mark.asyncio
    async def test_face_reference_with_pose(self, mock_config, mock_cos_client, mock_reactor_client):
        result = await acquire_first_frame(
            workflow_id="wf_faceref",
            mode="face_reference",
            uploaded_first_frame=None,
            reference_image="https://cdn.example.com/user_ref.png",
            analysis_result={"reference_image": "https://cdn.example.com/pose.png"},
            face_swap_config={},
            is_continuation=False,
            parent_workflow=None,
            config=mock_config,
            redis=MagicMock(),
            cos_client=mock_cos_client,
            reactor_client=mock_reactor_client,
        )
        assert result.url == "https://cdn.example.com/pose.png"
        assert result.source == "pose_reference"

    @pytest.mark.asyncio
    async def test_face_reference_reactor_deferred(self, mock_config, mock_cos_client, mock_reactor_client):
        """When skip_reactor flag is set, reactor should be deferred."""
        result = await acquire_first_frame(
            workflow_id="wf_defer",
            mode="face_reference",
            uploaded_first_frame=None,
            reference_image="https://cdn.example.com/ref.png",
            analysis_result={
                "reference_image": "https://cdn.example.com/pose.png",
                "reference_skip_reactor": True,
            },
            face_swap_config={"enabled": True, "strength": 1.0},
            is_continuation=False,
            parent_workflow=None,
            config=mock_config,
            redis=MagicMock(),
            cos_client=mock_cos_client,
            reactor_client=mock_reactor_client,
        )
        assert result.reactor_deferred is True
        assert result.face_swapped is False
        # Reactor should NOT have been called
        mock_reactor_client.swap_face.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_mode_raises(self, mock_config, mock_cos_client, mock_reactor_client):
        with pytest.raises(ValueError, match="Unknown mode"):
            await acquire_first_frame(
                workflow_id="wf_bad",
                mode="invalid_mode",
                uploaded_first_frame=None,
                reference_image=None,
                analysis_result={},
                face_swap_config={},
                is_continuation=False,
                parent_workflow=None,
                config=mock_config,
                redis=MagicMock(),
                cos_client=mock_cos_client,
                reactor_client=mock_reactor_client,
            )

    @pytest.mark.asyncio
    async def test_continuation_no_parent_raises(self, mock_config, mock_cos_client, mock_reactor_client):
        with pytest.raises(RuntimeError, match="no parent workflow"):
            await acquire_first_frame(
                workflow_id="wf_err",
                mode="t2v",
                uploaded_first_frame=None,
                reference_image=None,
                analysis_result={},
                face_swap_config={},
                is_continuation=True,
                parent_workflow=None,
                config=mock_config,
                redis=MagicMock(),
                cos_client=mock_cos_client,
                reactor_client=mock_reactor_client,
            )
