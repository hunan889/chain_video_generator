"""Tests for Stage 3 -- SeeDream Editing.

Uses mocked external services (BytePlus, Reactor, COS).
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_gateway.services.stages.seedream_edit import (
    SeeDreamResult,
    _compute_seedream_size,
    _resolve_legacy_mode_prompt,
    _resolve_seedream_prompt,
    build_seedream_prompt,
    edit_first_frame,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.byteplus_api_key = "fake-key"
    cfg.byteplus_api_url = "https://api.byteplus.com"
    return cfg


@pytest.fixture
def mock_cos_client():
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
def mock_byteplus_client():
    client = MagicMock()
    client.model = "seedream-2.0"
    client.generate_image = AsyncMock(
        return_value="https://byteplus-result.com/output.jpg"
    )
    return client


@pytest.fixture
def mock_reactor_client():
    client = MagicMock()
    client.swap_face = AsyncMock(return_value=b"\x89PNG_REACTOR_RESULT")
    return client


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


class TestBuildSeeDreamPrompt:
    """Tests for build_seedream_prompt."""

    def test_default_toggles(self):
        prompt = build_seedream_prompt(swap_face=True)
        assert "swap face to image 1" in prompt
        assert "keep accessories the same" in prompt
        assert "keep background" in prompt

    def test_full_swap(self):
        prompt = build_seedream_prompt(
            swap_face=True,
            swap_accessories=True,
            swap_expression=True,
            swap_clothing=True,
        )
        assert "swap face" in prompt
        assert "change accessories" in prompt
        assert "change facial expression" in prompt
        assert "change clothing" in prompt

    def test_no_swap(self):
        prompt = build_seedream_prompt(
            swap_face=False,
            swap_accessories=False,
            swap_expression=False,
            swap_clothing=False,
        )
        assert "keep face identity the same" in prompt
        assert "keep accessories the same" in prompt
        assert "preserve the facial expression" in prompt
        assert "keep clothing the same" in prompt


class TestResolveSeeDreamPrompt:
    """Tests for _resolve_seedream_prompt."""

    def test_custom_prompt_takes_priority(self):
        config = {"prompt": "custom edit instruction"}
        result = _resolve_seedream_prompt(config, "")
        assert result == "custom edit instruction"

    def test_toggle_based_prompt(self):
        config = {"swap_face": True, "swap_clothing": True}
        result = _resolve_seedream_prompt(config, "")
        assert "swap face" in result
        assert "change clothing" in result

    def test_user_prompt_appended(self):
        config = {"swap_face": True}
        result = _resolve_seedream_prompt(config, "make it dramatic")
        assert result.endswith(". make it dramatic")

    def test_legacy_mode(self):
        config = {"mode": "full_body"}
        result = _resolve_seedream_prompt(config, "")
        assert "swap face" in result
        assert "change clothing" in result


class TestResolveLegacyModePrompt:
    """Tests for _resolve_legacy_mode_prompt."""

    def test_face_only(self):
        prompt = _resolve_legacy_mode_prompt("face_only")
        assert "swap face" in prompt
        assert "keep accessories the same" in prompt

    def test_face_wearings(self):
        prompt = _resolve_legacy_mode_prompt("face_wearings")
        assert "swap face" in prompt
        assert "change accessories" in prompt

    def test_full_body(self):
        prompt = _resolve_legacy_mode_prompt("full_body")
        assert "swap face" in prompt
        assert "change clothing" in prompt

    def test_unknown_defaults_to_face_wearings(self):
        prompt = _resolve_legacy_mode_prompt("unknown_mode")
        assert "swap face" in prompt
        assert "change accessories" in prompt


class TestComputeSeeDreamSize:
    """Tests for _compute_seedream_size."""

    def test_portrait_3_4(self):
        size = _compute_seedream_size("720p_3_4", None)
        parts = size.split("x")
        assert len(parts) == 2
        w, h = int(parts[0]), int(parts[1])
        assert w < h  # portrait
        assert w % 8 == 0
        assert h % 8 == 0

    def test_landscape_16_9(self):
        size = _compute_seedream_size("720p_16_9", None)
        parts = size.split("x")
        w, h = int(parts[0]), int(parts[1])
        assert w > h  # landscape
        assert w % 8 == 0

    def test_explicit_aspect_ratio(self):
        size = _compute_seedream_size("480p", "16:9")
        parts = size.split("x")
        w, h = int(parts[0]), int(parts[1])
        assert w > h

    def test_minimum_720p(self):
        size = _compute_seedream_size("480p_3_4", None)
        parts = size.split("x")
        w, h = int(parts[0]), int(parts[1])
        # SeeDream minimum is 720p, so dimensions should be >= 720 on the larger side
        assert max(w, h) >= 720


# ---------------------------------------------------------------------------
# edit_first_frame tests
# ---------------------------------------------------------------------------


class TestEditFirstFrame:
    """Tests for the main edit_first_frame function."""

    @pytest.mark.asyncio
    async def test_skip_for_t2v_mode(self, mock_config, mock_cos_client, mock_byteplus_client, mock_reactor_client):
        result = await edit_first_frame(
            workflow_id="wf_t2v",
            first_frame_url="https://cdn.example.com/frame.png",
            reference_image_url=None,
            mode="t2v",
            seedream_config={},
            face_swap_config={},
            user_prompt="",
            reactor_deferred=False,
            is_continuation=False,
            resolution="480p_3_4",
            aspect_ratio=None,
            config=mock_config,
            byteplus_client=mock_byteplus_client,
            reactor_client=mock_reactor_client,
            cos_client=mock_cos_client,
        )
        assert result.skipped is True
        assert result.url is None

    @pytest.mark.asyncio
    async def test_skip_for_first_frame_mode(self, mock_config, mock_cos_client, mock_byteplus_client, mock_reactor_client):
        result = await edit_first_frame(
            workflow_id="wf_ff",
            first_frame_url="https://cdn.example.com/frame.png",
            reference_image_url=None,
            mode="first_frame",
            seedream_config={},
            face_swap_config={},
            user_prompt="",
            reactor_deferred=False,
            is_continuation=False,
            resolution="480p_3_4",
            aspect_ratio=None,
            config=mock_config,
            byteplus_client=mock_byteplus_client,
            reactor_client=mock_reactor_client,
            cos_client=mock_cos_client,
        )
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_skip_for_continuation(self, mock_config, mock_cos_client, mock_byteplus_client, mock_reactor_client):
        result = await edit_first_frame(
            workflow_id="wf_cont",
            first_frame_url="https://cdn.example.com/frame.png",
            reference_image_url="https://cdn.example.com/ref.png",
            mode="full_body_reference",
            seedream_config={"enabled": True},
            face_swap_config={},
            user_prompt="",
            reactor_deferred=False,
            is_continuation=True,
            resolution="480p_3_4",
            aspect_ratio=None,
            config=mock_config,
            byteplus_client=mock_byteplus_client,
            reactor_client=mock_reactor_client,
            cos_client=mock_cos_client,
        )
        assert result.skipped is True
        assert "continuation" in result.skip_reason

    @pytest.mark.asyncio
    async def test_full_body_reference_runs_seedream(self, mock_config, mock_cos_client, mock_byteplus_client, mock_reactor_client):
        """full_body_reference mode should always run SeeDream."""
        with patch(
            "api_gateway.services.stages.seedream_edit._download_image_as_b64",
            new=AsyncMock(return_value=base64.b64encode(b"fake").decode()),
        ), patch(
            "api_gateway.services.stages.seedream_edit._download_image_bytes",
            new=AsyncMock(return_value=b"\x89PNG_RESULT"),
        ):
            result = await edit_first_frame(
                workflow_id="wf_fbr",
                first_frame_url="https://cdn.example.com/frame.png",
                reference_image_url="https://cdn.example.com/ref.png",
                mode="full_body_reference",
                seedream_config={"enabled": True, "swap_face": True, "swap_clothing": True},
                face_swap_config={},
                user_prompt="",
                reactor_deferred=False,
                is_continuation=False,
                resolution="720p_3_4",
                aspect_ratio=None,
                config=mock_config,
                byteplus_client=mock_byteplus_client,
                reactor_client=mock_reactor_client,
                cos_client=mock_cos_client,
            )

        assert result.skipped is False
        assert result.url is not None
        assert result.api_status == "success"
        assert result.model == "seedream-2.0"
        mock_byteplus_client.generate_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_face_reference_disabled(self, mock_config, mock_cos_client, mock_byteplus_client, mock_reactor_client):
        """face_reference mode with SeeDream disabled should skip."""
        result = await edit_first_frame(
            workflow_id="wf_fr_off",
            first_frame_url="https://cdn.example.com/frame.png",
            reference_image_url="https://cdn.example.com/ref.png",
            mode="face_reference",
            seedream_config={"enabled": False},
            face_swap_config={},
            user_prompt="",
            reactor_deferred=False,
            is_continuation=False,
            resolution="480p_3_4",
            aspect_ratio=None,
            config=mock_config,
            byteplus_client=mock_byteplus_client,
            reactor_client=mock_reactor_client,
            cos_client=mock_cos_client,
        )
        assert result.skipped is True

    @pytest.mark.asyncio
    async def test_deferred_reactor_on_skip(self, mock_config, mock_cos_client, mock_byteplus_client, mock_reactor_client):
        """When SeeDream is skipped but reactor was deferred, Reactor should run."""
        with patch(
            "api_gateway.services.stages.seedream_edit._download_image_as_b64",
            new=AsyncMock(return_value=base64.b64encode(b"fake").decode()),
        ):
            result = await edit_first_frame(
                workflow_id="wf_defer",
                first_frame_url="https://cdn.example.com/frame.png",
                reference_image_url="https://cdn.example.com/ref.png",
                mode="first_frame",
                seedream_config={},
                face_swap_config={"enabled": True, "strength": 0.9},
                user_prompt="",
                reactor_deferred=True,
                is_continuation=False,
                resolution="480p_3_4",
                aspect_ratio=None,
                config=mock_config,
                byteplus_client=mock_byteplus_client,
                reactor_client=mock_reactor_client,
                cos_client=mock_cos_client,
            )
        assert result.face_swapped is True
        mock_reactor_client.swap_face.assert_called_once()

    @pytest.mark.asyncio
    async def test_seedream_failure_fallback_to_reactor(self, mock_config, mock_cos_client, mock_reactor_client):
        """When SeeDream fails for face_reference mode, Reactor should be tried as fallback."""
        # BytePlus client that fails
        failing_byteplus = MagicMock()
        failing_byteplus.model = "seedream-2.0"
        failing_byteplus.generate_image = AsyncMock(side_effect=RuntimeError("API error"))

        with patch(
            "api_gateway.services.stages.seedream_edit._download_image_as_b64",
            new=AsyncMock(return_value=base64.b64encode(b"fake").decode()),
        ):
            result = await edit_first_frame(
                workflow_id="wf_fail",
                first_frame_url="https://cdn.example.com/frame.png",
                reference_image_url="https://cdn.example.com/ref.png",
                mode="face_reference",
                seedream_config={"enabled": True, "swap_face": True},
                face_swap_config={"enabled": True, "strength": 1.0},
                user_prompt="",
                reactor_deferred=True,
                is_continuation=False,
                resolution="720p_3_4",
                aspect_ratio=None,
                config=mock_config,
                byteplus_client=failing_byteplus,
                reactor_client=mock_reactor_client,
                cos_client=mock_cos_client,
            )

        assert result.fallback_used is True
        assert result.face_swapped is True
        assert result.error is not None
        mock_reactor_client.swap_face.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_reference_image_skips(self, mock_config, mock_cos_client, mock_byteplus_client, mock_reactor_client):
        """No reference image should skip SeeDream even in full_body_reference mode."""
        result = await edit_first_frame(
            workflow_id="wf_noref",
            first_frame_url="https://cdn.example.com/frame.png",
            reference_image_url=None,
            mode="full_body_reference",
            seedream_config={"enabled": True},
            face_swap_config={},
            user_prompt="",
            reactor_deferred=False,
            is_continuation=False,
            resolution="720p_3_4",
            aspect_ratio=None,
            config=mock_config,
            byteplus_client=mock_byteplus_client,
            reactor_client=mock_reactor_client,
            cos_client=mock_cos_client,
        )
        assert result.skipped is True
        assert "no reference image" in result.skip_reason
