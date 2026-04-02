"""Tests for Stage 4 -- Video Generation.

Uses fakeredis and mocked dependencies.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from shared.enums import GenerateMode, ModelType, TaskStatus
from shared.redis_keys import chain_key, task_key
from shared.task_gateway import TaskGateway

from api_gateway.services.stages.video_generation import (
    VideoGenerationResult,
    _build_lora_context,
    _collect_trigger_words,
    _filter_loras_by_mode,
    _normalize_lora_weights,
    generate_video,
    parse_resolution,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.flushall()
    await r.aclose()


@pytest_asyncio.fixture
async def gateway(redis):
    return TaskGateway(redis)


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.llm_api_key = ""
    cfg.llm_base_url = ""
    cfg.llm_model = ""
    cfg.vision_api_key = ""
    cfg.vision_base_url = ""
    cfg.vision_model = ""
    cfg.workflows_dir = ""
    cfg.cos_prefix = "test"
    cfg.loras_yaml_path = ""
    return cfg


@pytest.fixture
def mock_cos_client():
    client = MagicMock()
    client.upload_file = MagicMock(return_value="https://cdn.example.com/test.png")
    return client


# ---------------------------------------------------------------------------
# Resolution parsing tests
# ---------------------------------------------------------------------------


class TestParseResolution:
    """Tests for parse_resolution."""

    def test_portrait_480p_3_4(self):
        w, h = parse_resolution("480p_3_4")
        assert w < h
        assert w % 16 == 0
        assert h % 16 == 0

    def test_landscape_720p_16_9(self):
        w, h = parse_resolution("720p_16_9")
        assert w > h
        assert w % 16 == 0
        assert h % 16 == 0

    def test_colon_separator(self):
        w, h = parse_resolution("480p_3:4")
        assert w < h
        assert w % 16 == 0

    def test_unknown_resolution_defaults(self):
        w, h = parse_resolution("unknown")
        assert w == 832
        assert h == 480

    def test_720p_3_4(self):
        w, h = parse_resolution("720p_3_4")
        assert w < h
        # 720p portrait: width=720 (rounded to 16), height ~ 960
        assert w == 720
        assert h >= 944  # 720 * 4/3 = 960, rounded to 16

    def test_square_resolution(self):
        w, h = parse_resolution("512p_1_1")
        assert w == h
        assert w % 16 == 0


# ---------------------------------------------------------------------------
# LoRA helper tests
# ---------------------------------------------------------------------------


class TestLoraHelpers:
    """Tests for LoRA-related helper functions."""

    def test_build_lora_context_empty(self):
        assert _build_lora_context([]) is None

    def test_build_lora_context(self):
        loras = [
            {"name": "lora1", "trigger_words": ["kw1", "kw2"], "description": "desc"},
            {"name": "lora2", "trigger_words": '["kw3"]'},
        ]
        result = _build_lora_context(loras)
        assert len(result) == 2
        assert result[0]["name"] == "lora1"
        assert result[0]["description"] == "kw1, kw2"
        assert result[1]["name"] == "lora2"

    def test_collect_trigger_words(self):
        loras = [
            {"trigger_words": ["alpha", "beta"]},
            {"trigger_words": '["beta", "gamma"]'},
        ]
        words = _collect_trigger_words(loras)
        assert words == ["alpha", "beta", "gamma"]

    def test_collect_trigger_words_empty(self):
        assert _collect_trigger_words([]) == []

    def test_filter_loras_i2v(self):
        loras = [
            {"name": "a", "mode": "I2V", "noise_stage": "high"},
            {"name": "b", "mode": "T2V", "noise_stage": ""},
            {"name": "c", "mode": "", "noise_stage": "single"},
        ]
        result = _filter_loras_by_mode(loras, is_i2v=True)
        names = [l["name"] for l in result]
        assert "a" in names
        assert "c" in names
        assert "b" not in names

    def test_filter_loras_t2v(self):
        loras = [
            {"name": "a", "mode": "I2V"},
            {"name": "b", "mode": "T2V"},
            {"name": "c", "mode": ""},
        ]
        result = _filter_loras_by_mode(loras, is_i2v=False)
        names = [l["name"] for l in result]
        assert "b" in names
        assert "c" in names
        assert "a" not in names

    def test_filter_loras_fallback_all(self):
        """If no loras match the filter, return all."""
        loras = [
            {"name": "a", "mode": "I2V"},
        ]
        result = _filter_loras_by_mode(loras, is_i2v=False)
        assert len(result) == 1  # Fallback to all

    def test_normalize_weights_no_change(self):
        loras = [{"name": "a", "weight": 0.5}, {"name": "b", "weight": 0.3}]
        result = _normalize_lora_weights(loras, max_total=1.0)
        assert result[0]["weight"] == 0.5
        assert result[1]["weight"] == 0.3

    def test_normalize_weights_scaled(self):
        loras = [{"name": "a", "weight": 0.8}, {"name": "b", "weight": 0.8}]
        result = _normalize_lora_weights(loras, max_total=1.0)
        total = sum(l["weight"] for l in result)
        assert total <= 1.01  # Allow rounding tolerance

    def test_normalize_weights_immutable(self):
        """Original list should not be modified."""
        loras = [{"name": "a", "weight": 0.8}, {"name": "b", "weight": 0.8}]
        original_weights = [l["weight"] for l in loras]
        _normalize_lora_weights(loras, max_total=1.0)
        assert [l["weight"] for l in loras] == original_weights


# ---------------------------------------------------------------------------
# Video generation integration test
# ---------------------------------------------------------------------------


class TestGenerateVideo:
    """Integration tests for generate_video with fakeredis."""

    @pytest.mark.asyncio
    async def test_t2v_generation(self, redis, gateway, mock_config, mock_cos_client):
        """Basic T2V generation should create chain + task and poll for completion."""
        # Create workflow hash
        wf_id = "test_wf_t2v"
        await redis.hset(f"workflow:{wf_id}", "status", "running")

        # Background task to simulate completion
        async def simulate_completion():
            for _ in range(100):
                await asyncio.sleep(0.1)
                # Find chain keys
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match="chain:*", count=50)
                    for key in keys:
                        status = await redis.hget(key, "status")
                        if status == "running":
                            # Find and complete the task
                            task_ids = json.loads(await redis.hget(key, "segment_task_ids") or "[]")
                            for tid in task_ids:
                                t_status = await redis.hget(task_key(tid), "status")
                                if t_status == TaskStatus.QUEUED.value:
                                    await redis.hset(task_key(tid), mapping={
                                        "status": TaskStatus.COMPLETED.value,
                                        "video_url": "https://cdn.example.com/video.mp4",
                                        "completed_at": str(int(time.time())),
                                    })
                                    # Also complete the chain
                                    await redis.hset(key, mapping={
                                        "status": "completed",
                                        "final_video_url": "https://cdn.example.com/video.mp4",
                                        "completed_at": str(int(time.time())),
                                    })
                                    return
                    if cursor == 0:
                        break

        helper = asyncio.create_task(simulate_completion())

        with patch("shared.workflow_builder.build_workflow", side_effect=Exception("no templates")), \
             patch("api_gateway.services.stages.video_generation._MAX_POLL_SECONDS", 30), \
             patch("api_gateway.services.stages.video_generation._POLL_INTERVAL", 0.2):
            result = await generate_video(
                workflow_id=wf_id,
                mode="t2v",
                first_frame_url=None,
                analysis_result={"video_prompt": "A cat walking in the garden"},
                internal_config={
                    "stage4_video": {
                        "generation": {
                            "model": "A14B",
                            "resolution": "480p_3_4",
                            "duration": "5s",
                            "steps": 20,
                            "cfg": 6.0,
                        },
                    },
                    "stage1_prompt_analysis": {"auto_prompt": False, "auto_lora": False},
                },
                user_prompt="A cat walking",
                is_continuation=False,
                parent_workflow=None,
                origin_first_frame_url=None,
                config=mock_config,
                gateway=gateway,
                cos_client=mock_cos_client,
                redis=redis,
            )

        helper.cancel()
        try:
            await helper
        except asyncio.CancelledError:
            pass

        assert result.chain_id is not None
        assert result.video_url == "https://cdn.example.com/video.mp4"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, redis, gateway, mock_config, mock_cos_client):
        """If chain doesn't complete in time, result should have an error."""
        wf_id = "test_wf_timeout"
        await redis.hset(f"workflow:{wf_id}", "status", "running")

        with patch("shared.workflow_builder.build_workflow", side_effect=Exception("no templates")), \
             patch("api_gateway.services.stages.video_generation._MAX_POLL_SECONDS", 1), \
             patch("api_gateway.services.stages.video_generation._POLL_INTERVAL", 0.2):
            result = await generate_video(
                workflow_id=wf_id,
                mode="t2v",
                first_frame_url=None,
                analysis_result={},
                internal_config={
                    "stage4_video": {
                        "generation": {
                            "model": "A14B",
                            "resolution": "480p_3_4",
                            "duration": "5s",
                        },
                    },
                    "stage1_prompt_analysis": {"auto_prompt": False, "auto_lora": False},
                },
                user_prompt="A test",
                is_continuation=False,
                parent_workflow=None,
                origin_first_frame_url=None,
                config=mock_config,
                gateway=gateway,
                cos_client=mock_cos_client,
                redis=redis,
            )

        assert result.error is not None
        assert "timeout" in result.error.lower()
        assert result.chain_id is not None

    @pytest.mark.asyncio
    async def test_chain_failure_returns_error(self, redis, gateway, mock_config, mock_cos_client):
        """If the chain fails, result should capture the error."""
        wf_id = "test_wf_fail"
        await redis.hset(f"workflow:{wf_id}", "status", "running")

        async def simulate_failure():
            for _ in range(200):
                await asyncio.sleep(0.1)
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match="chain:*", count=50)
                    for key in keys:
                        status = await redis.hget(key, "status")
                        if status == "running":
                            # Fail the task (poll loop checks task status, not chain)
                            task_ids = json.loads(await redis.hget(key, "segment_task_ids") or "[]")
                            for tid in task_ids:
                                await redis.hset(task_key(tid), mapping={
                                    "status": TaskStatus.FAILED.value,
                                    "error": "CUDA out of memory",
                                    "completed_at": str(int(time.time())),
                                })
                            await redis.hset(key, mapping={
                                "status": "failed",
                                "error": "CUDA out of memory",
                                "completed_at": str(int(time.time())),
                            })
                            return
                    if cursor == 0:
                        break

        helper = asyncio.create_task(simulate_failure())

        with patch("shared.workflow_builder.build_workflow", side_effect=Exception("no templates")), \
             patch("api_gateway.services.stages.video_generation._MAX_POLL_SECONDS", 30), \
             patch("api_gateway.services.stages.video_generation._POLL_INTERVAL", 0.2):
            result = await generate_video(
                workflow_id=wf_id,
                mode="t2v",
                first_frame_url=None,
                analysis_result={},
                internal_config={
                    "stage4_video": {
                        "generation": {
                            "model": "A14B",
                            "resolution": "480p_3_4",
                            "duration": "5s",
                        },
                    },
                    "stage1_prompt_analysis": {"auto_prompt": False, "auto_lora": False},
                },
                user_prompt="A test",
                is_continuation=False,
                parent_workflow=None,
                origin_first_frame_url=None,
                config=mock_config,
                gateway=gateway,
                cos_client=mock_cos_client,
                redis=redis,
            )

        helper.cancel()
        try:
            await helper
        except asyncio.CancelledError:
            pass

        assert result.error is not None
        assert "CUDA" in result.error

    @pytest.mark.asyncio
    async def test_continuation_inherits_parent_params(self, redis, gateway, mock_config, mock_cos_client):
        """Continuation should inherit parent's generation params and dimensions."""
        wf_id = "test_wf_cont"
        await redis.hset(f"workflow:{wf_id}", "status", "running")

        parent = {
            "actual_width": "720",
            "actual_height": "960",
            "final_video_url": "https://cdn.example.com/parent.mp4",
            "chain_id": "parent_chain_123",
            "internal_config": json.dumps({
                "stage4_video": {
                    "generation": {
                        "model": "A14B",
                        "steps": 30,
                        "cfg": 7.0,
                        "scheduler": "euler",
                    }
                }
            }),
        }

        async def simulate_completion():
            for _ in range(100):
                await asyncio.sleep(0.1)
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match="chain:*", count=50)
                    for key in keys:
                        status = await redis.hget(key, "status")
                        if status == "running":
                            task_ids = json.loads(await redis.hget(key, "segment_task_ids") or "[]")
                            for tid in task_ids:
                                t_status = await redis.hget(task_key(tid), "status")
                                if t_status == TaskStatus.QUEUED.value:
                                    await redis.hset(task_key(tid), mapping={
                                        "status": TaskStatus.COMPLETED.value,
                                        "video_url": "https://cdn.example.com/cont.mp4",
                                        "completed_at": str(int(time.time())),
                                    })
                                    await redis.hset(key, mapping={
                                        "status": "completed",
                                        "final_video_url": "https://cdn.example.com/cont.mp4",
                                        "completed_at": str(int(time.time())),
                                    })
                                    return
                    if cursor == 0:
                        break

        helper = asyncio.create_task(simulate_completion())

        with patch("shared.workflow_builder.build_workflow", side_effect=Exception("no templates")), \
             patch("api_gateway.services.stages.video_generation._MAX_POLL_SECONDS", 30), \
             patch("api_gateway.services.stages.video_generation._POLL_INTERVAL", 0.2):
            result = await generate_video(
                workflow_id=wf_id,
                mode="t2v",
                first_frame_url="https://cdn.example.com/last_frame.png",
                analysis_result={},
                internal_config={
                    "stage4_video": {
                        "generation": {
                            "model": "A14B",
                            "resolution": "480p_3_4",
                            "duration": "5s",
                        },
                    },
                    "stage1_prompt_analysis": {"auto_prompt": False, "auto_lora": False},
                },
                user_prompt="Continue the scene",
                is_continuation=True,
                parent_workflow=parent,
                origin_first_frame_url="https://cdn.example.com/origin.png",
                config=mock_config,
                gateway=gateway,
                cos_client=mock_cos_client,
                redis=redis,
            )

        helper.cancel()
        try:
            await helper
        except asyncio.CancelledError:
            pass

        assert result.video_url == "https://cdn.example.com/cont.mp4"
        # Check that actual dimensions were stored in workflow hash
        aw = await redis.hget(f"workflow:{wf_id}", "actual_width")
        ah = await redis.hget(f"workflow:{wf_id}", "actual_height")
        assert aw == "720"
        assert ah == "960"
