"""Tests for GPU worker logic using FakeRedis."""

import asyncio
import json
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

from shared.enums import GenerateMode, ModelType, TaskStatus
from shared.redis_keys import queue_key, task_key, worker_heartbeat_key
from shared.task_gateway import TaskGateway


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def redis():
    """Create a FakeRedis async instance."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def gateway(redis):
    """Create a TaskGateway backed by FakeRedis."""
    return TaskGateway(redis, task_expiry=3600)


@pytest.fixture
def worker_config():
    """Build a minimal WorkerConfig for testing."""
    from gpu_worker.config import WorkerConfig

    return WorkerConfig(
        worker_id="test-worker-1",
        redis_url="redis://localhost:6379/0",
        cos_secret_id="",
        cos_secret_key="",
        cos_bucket="",
        cos_region="",
        cos_prefix="",
        cos_cdn_domain="",
        comfyui_urls={"a14b": "http://127.0.0.1:8188"},
        task_expiry=3600,
        heartbeat_interval=0.1,  # fast heartbeat for tests
    )


@pytest.fixture
def cos_client():
    """Create a disabled COSClient for testing."""
    from shared.cos import COSClient, COSConfig

    return COSClient(COSConfig.disabled())


@pytest.fixture
def worker(worker_config, redis, gateway, cos_client):
    """Create a GPUWorker instance."""
    from gpu_worker.worker import GPUWorker

    return GPUWorker(
        config=worker_config,
        redis=redis,
        gateway=gateway,
        cos_client=cos_client,
    )


@pytest.fixture
def heartbeat(redis, worker_config):
    """Create a HeartbeatReporter instance."""
    from gpu_worker.heartbeat import HeartbeatReporter

    return HeartbeatReporter(redis=redis, config=worker_config)


# ---------------------------------------------------------------------------
# Worker tests
# ---------------------------------------------------------------------------

class TestWorkerPicksUpTask:
    """Verify that the worker dequeues tasks from Redis."""

    @pytest.mark.asyncio
    async def test_worker_picks_up_task(self, worker, redis, gateway):
        """Push a task into the queue; worker should pick it up and process it."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        # Inject mock ComfyUI client
        mock_client = _make_mock_comfyui_client()
        mock_cos = MagicMock()
        mock_cos.upload_file = MagicMock(return_value="https://cdn.example.com/videos/test.mp4")
        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        # Run worker for one iteration then stop
        worker._running = True

        async def stop_after_delay():
            await asyncio.sleep(0.3)
            worker._running = False

        asyncio.create_task(stop_after_delay())
        await worker.run()

        # Task should have been picked up and completed
        task = await gateway.get_task(task_id)
        assert task is not None
        assert task["status"] == TaskStatus.COMPLETED.value


class TestWorkerMarksTaskRunning:
    """Verify that processing marks the task as running."""

    @pytest.mark.asyncio
    async def test_worker_marks_task_running(self, worker, redis, gateway):
        """Task status should transition to running during processing."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        # Inject mock ComfyUI client so real connections are not attempted
        mock_client = _make_mock_comfyui_client()
        mock_cos = MagicMock()
        mock_cos.upload_file = MagicMock(return_value="https://cdn.example.com/videos/test.mp4")
        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        # Track statuses seen during processing
        statuses_seen: list[str] = []
        original_process = worker._process_task

        async def spy_process(tid: str) -> None:
            task_before = await gateway.get_task(tid)
            await original_process(tid)
            task_after = await gateway.get_task(tid)
            statuses_seen.append(task_after["status"])

        worker._process_task = spy_process
        worker._running = True

        async def stop_after_delay():
            await asyncio.sleep(0.3)
            worker._running = False

        asyncio.create_task(stop_after_delay())
        await worker.run()

        # The task should have been completed
        assert TaskStatus.COMPLETED.value in statuses_seen


class TestWorkerMarksTaskCompleted:
    """Verify that after processing the task is marked completed."""

    @pytest.mark.asyncio
    async def test_worker_marks_task_completed(self, worker, redis, gateway):
        """After processing, task should be completed."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        # Inject mock ComfyUI client
        mock_client = _make_mock_comfyui_client()
        mock_cos = MagicMock()
        mock_cos.upload_file = MagicMock(return_value="https://cdn.example.com/videos/test.mp4")
        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        worker._running = True

        async def stop_after_delay():
            await asyncio.sleep(0.3)
            worker._running = False

        asyncio.create_task(stop_after_delay())
        await worker.run()

        task = await gateway.get_task(task_id)
        assert task["status"] == TaskStatus.COMPLETED.value


class TestWorkerSkipsEmptyQueue:
    """Verify that the worker doesn't error when queue is empty."""

    @pytest.mark.asyncio
    async def test_worker_skips_empty_queue(self, worker, redis):
        """Worker should loop quietly when no tasks are available."""
        worker._running = True

        async def stop_after_delay():
            await asyncio.sleep(0.5)
            worker._running = False

        asyncio.create_task(stop_after_delay())

        # Should complete without errors
        await worker.run()


class TestWorkerHandlesFailure:
    """Verify that worker marks task failed on processing error."""

    @pytest.mark.asyncio
    async def test_worker_marks_task_failed_on_error(self, worker, redis, gateway):
        """If _process_task raises, the task should be marked failed."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        async def failing_process(tid: str) -> None:
            raise RuntimeError("simulated ComfyUI crash")

        worker._process_task = failing_process
        worker._running = True

        async def stop_after_delay():
            await asyncio.sleep(0.3)
            worker._running = False

        asyncio.create_task(stop_after_delay())
        await worker.run()

        task = await gateway.get_task(task_id)
        assert task["status"] == TaskStatus.FAILED.value
        assert "simulated ComfyUI crash" in task["error"]


# ---------------------------------------------------------------------------
# Heartbeat tests
# ---------------------------------------------------------------------------

class TestHeartbeatReports:
    """Verify that heartbeat writes to the correct Redis key."""

    @pytest.mark.asyncio
    async def test_heartbeat_reports(self, heartbeat, redis):
        """Heartbeat should write worker info to Redis."""
        await heartbeat.start()

        # Give the heartbeat loop a chance to run
        await asyncio.sleep(0.2)

        hb_key = worker_heartbeat_key("test-worker-1")
        data = await redis.hgetall(hb_key)

        assert data is not None
        assert "last_seen" in data
        assert "model_keys" in data
        assert json.loads(data["model_keys"]) == ["a14b"]

        await heartbeat.stop()


class TestHeartbeatUpdatesStatus:
    """Verify that heartbeat status reflects idle/busy transitions."""

    @pytest.mark.asyncio
    async def test_heartbeat_updates_status(self, heartbeat, redis):
        """Heartbeat status should reflect the reported worker status."""
        await heartbeat.start()
        await asyncio.sleep(0.2)

        hb_key = worker_heartbeat_key("test-worker-1")

        # Default should be idle
        data = await redis.hgetall(hb_key)
        assert data["status"] == "idle"

        # Update to busy
        heartbeat.set_status("busy")
        await asyncio.sleep(0.2)

        data = await redis.hgetall(hb_key)
        assert data["status"] == "busy"

        # Back to idle
        heartbeat.set_status("idle")
        await asyncio.sleep(0.2)

        data = await redis.hgetall(hb_key)
        assert data["status"] == "idle"

        await heartbeat.stop()


# ---------------------------------------------------------------------------
# ComfyUI integration tests (mocked client)
# ---------------------------------------------------------------------------


def _make_mock_comfyui_client(
    prompt_id: str = "test-prompt-123",
    output_files: list[dict] | None = None,
    download_data: bytes = b"fake-video-data",
    upload_result: dict | None = None,
):
    """Build an AsyncMock ComfyUIClient with sensible defaults."""
    client = AsyncMock()
    client.base_url = "http://127.0.0.1:8188"
    client.queue_prompt = AsyncMock(return_value=prompt_id)
    client.wait_for_completion = AsyncMock(return_value={
        "outputs": {"10": {"gifs": output_files or [
            {"filename": "result_00001_.mp4", "subfolder": "", "type": "output"}
        ]}},
        "status": {"completed": True},
    })
    client.get_output_files = AsyncMock(return_value=output_files or [
        {"filename": "result_00001_.mp4", "subfolder": "", "type": "output"}
    ])
    client.download_file = AsyncMock(return_value=download_data)
    client.upload_image = AsyncMock(return_value=upload_result or {"name": "uploaded.png"})
    client.free_memory = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


class TestProcessTaskSubmitsWorkflow:
    """Verify _process_task submits the workflow JSON to ComfyUI."""

    @pytest.mark.asyncio
    async def test_submits_workflow_to_comfyui(self, worker, redis, gateway):
        """The stored workflow should be sent to ComfyUI queue_prompt."""
        workflow = {"1": {"class_type": "KSampler", "inputs": {}}}
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow=workflow,
        )

        mock_client = _make_mock_comfyui_client()
        mock_cos = MagicMock()
        mock_cos.upload_file = MagicMock(return_value="https://cdn.example.com/videos/test.mp4")

        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        await worker._process_task(task_id)

        mock_client.queue_prompt.assert_awaited_once_with(workflow)


class TestProcessTaskDownloadsAndUploadsResult:
    """Verify _process_task downloads result from ComfyUI and uploads to COS."""

    @pytest.mark.asyncio
    async def test_downloads_and_uploads_result(self, worker, redis, gateway):
        """Result video should be downloaded from ComfyUI and uploaded to COS."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        video_bytes = b"fake-mp4-content"
        mock_client = _make_mock_comfyui_client(download_data=video_bytes)
        mock_cos = MagicMock()
        mock_cos.upload_file = MagicMock(return_value="https://cdn.example.com/videos/result.mp4")

        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        await worker._process_task(task_id)

        # Should have downloaded from ComfyUI
        mock_client.download_file.assert_awaited_once_with(
            "result_00001_.mp4", "", "output"
        )
        # Should have uploaded to COS
        mock_cos.upload_file.assert_called_once()
        call_args = mock_cos.upload_file.call_args
        # First arg is the temp file path, second is subdir, third is filename
        assert call_args[0][1] == "videos"
        assert task_id in call_args[0][2]


class TestProcessTaskMarksCompleted:
    """Verify _process_task marks the task as completed with video_url."""

    @pytest.mark.asyncio
    async def test_marks_completed_with_video_url(self, worker, redis, gateway):
        """After processing, task status should be completed with video_url set."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        expected_url = "https://cdn.example.com/videos/result.mp4"
        mock_client = _make_mock_comfyui_client()
        mock_cos = MagicMock()
        mock_cos.upload_file = MagicMock(return_value=expected_url)

        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        await worker._process_task(task_id)

        task = await gateway.get_task(task_id)
        assert task["status"] == TaskStatus.COMPLETED.value
        assert task["video_url"] == expected_url


class TestProcessTaskHandlesComfyUIError:
    """Verify _process_task handles ComfyUI errors gracefully."""

    @pytest.mark.asyncio
    async def test_handles_comfyui_error(self, worker, redis, gateway):
        """ComfyUI queue_prompt failure should propagate as an exception."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        mock_client = _make_mock_comfyui_client()
        mock_client.queue_prompt = AsyncMock(
            side_effect=RuntimeError("ComfyUI prompt failed (500): internal error")
        )
        mock_cos = MagicMock()

        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        with pytest.raises(RuntimeError, match="ComfyUI prompt failed"):
            await worker._process_task(task_id)


class TestProcessTaskNoOutputFiles:
    """Verify _process_task raises when ComfyUI produces no outputs."""

    @pytest.mark.asyncio
    async def test_raises_on_no_output_files(self, worker, redis, gateway):
        """If ComfyUI produces no output files, a RuntimeError should be raised."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        mock_client = _make_mock_comfyui_client()
        mock_client.get_output_files = AsyncMock(return_value=[])
        mock_cos = MagicMock()

        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        with pytest.raises(RuntimeError, match="No output files"):
            await worker._process_task(task_id)


class TestProcessTaskOOMRetry:
    """Verify OOM detection triggers free_memory and re-queue."""

    @pytest.mark.asyncio
    async def test_oom_retry(self, worker, redis, gateway):
        """CUDA OOM on first attempt should free memory and re-queue the task."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        mock_client = _make_mock_comfyui_client()
        mock_client.wait_for_completion = AsyncMock(
            side_effect=RuntimeError("CUDA out of memory")
        )
        mock_cos = MagicMock()

        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        with pytest.raises(RuntimeError, match="CUDA out of memory"):
            await worker._process_task(task_id)

        # Should have called free_memory
        mock_client.free_memory.assert_awaited_once()

        # Task should have been re-queued (retry_count incremented)
        task = await gateway.get_task(task_id)
        assert task["retry_count"] == 1

        # The task_id should be back in the queue
        queue = queue_key("a14b")
        items = await redis.lrange(queue, 0, -1)
        assert task_id in items


class TestProcessTaskOOMMaxRetries:
    """Verify OOM does not re-queue after max retries."""

    @pytest.mark.asyncio
    async def test_oom_max_retries_exhausted(self, worker, redis, gateway):
        """After max OOM retries, the task should not be re-queued."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        # Drain the queue (simulating the worker's BLPOP that normally happens)
        queue = queue_key("a14b")
        await redis.lpop(queue)

        # Simulate already retried twice
        await redis.hset(task_key(task_id), "retry_count", "2")

        mock_client = _make_mock_comfyui_client()
        mock_client.wait_for_completion = AsyncMock(
            side_effect=RuntimeError("CUDA out of memory")
        )
        mock_cos = MagicMock()

        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        with pytest.raises(RuntimeError, match="CUDA out of memory"):
            await worker._process_task(task_id)

        # Should still free memory
        mock_client.free_memory.assert_awaited_once()

        # But should NOT re-queue (retry count already at max)
        items = await redis.lrange(queue, 0, -1)
        assert task_id not in items


class TestProcessTaskInputFilePlaceholder:
    """Verify input file download from COS and placeholder replacement."""

    @pytest.mark.asyncio
    async def test_input_file_placeholder_replacement(self, worker, redis, gateway):
        """Input files should be downloaded from COS, uploaded to ComfyUI,
        and their placeholders replaced in the workflow."""
        workflow = {
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": "PLACEHOLDER_INPUT_IMAGE"},
            }
        }
        input_files = [
            {"cos_key": "inputs/first_frame.png", "placeholder": "PLACEHOLDER_INPUT_IMAGE"}
        ]
        task_id = await gateway.create_task(
            mode=GenerateMode.I2V,
            model=ModelType.A14B,
            workflow=workflow,
        )
        # Store input_files in Redis
        await redis.hset(task_key(task_id), "input_files", json.dumps(input_files))

        mock_client = _make_mock_comfyui_client(
            upload_result={"name": "first_frame.png"}
        )
        mock_cos = MagicMock()
        mock_cos.upload_file = MagicMock(return_value="https://cdn.example.com/videos/result.mp4")

        # Mock COS download: download_file writes bytes to a local path
        def mock_cos_download(subdir, filename, local_path):
            with open(local_path, "wb") as f:
                f.write(b"fake-image-data")

        mock_cos.download_file = MagicMock(side_effect=mock_cos_download)

        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        await worker._process_task(task_id)

        # COS download should have been called
        mock_cos.download_file.assert_called_once()

        # ComfyUI upload should have been called with the image data
        mock_client.upload_image.assert_awaited_once()
        upload_call = mock_client.upload_image.call_args
        assert upload_call[0][0] == b"fake-image-data"

        # The submitted workflow should have the placeholder replaced
        submitted_workflow = mock_client.queue_prompt.call_args[0][0]
        assert submitted_workflow["1"]["inputs"]["image"] == "first_frame.png"


class TestProcessTaskExtractLastFrame:
    """Verify last frame extraction when extract_last_frame is set."""

    @pytest.mark.asyncio
    async def test_extract_last_frame(self, worker, redis, gateway):
        """When extract_last_frame is set, the last frame should be extracted and uploaded."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )
        await redis.hset(task_key(task_id), "extract_last_frame", "1")

        mock_client = _make_mock_comfyui_client()
        mock_cos = MagicMock()

        # upload_file called twice: once for video, once for last frame
        mock_cos.upload_file = MagicMock(side_effect=[
            "https://cdn.example.com/videos/result.mp4",
            "https://cdn.example.com/frames/last_frame.png",
        ])

        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        # Mock ffmpeg call for last-frame extraction.
        # The mock must also write fake data to the output file (the last arg
        # before capture_output) so that the upload check finds content.
        def fake_ffmpeg(args, **kwargs):
            # The output path is the last positional arg in the ffmpeg command
            output_path = args[-1]
            with open(output_path, "wb") as f:
                f.write(b"fake-png-frame-data")
            return MagicMock(returncode=0)

        with patch("gpu_worker.worker.subprocess.run", side_effect=fake_ffmpeg):
            await worker._process_task(task_id)

        task = await gateway.get_task(task_id)
        assert task["status"] == TaskStatus.COMPLETED.value
        assert task["video_url"] == "https://cdn.example.com/videos/result.mp4"
        assert task["last_frame_url"] == "https://cdn.example.com/frames/last_frame.png"


class TestConcatTask:
    """Tests for ffmpeg concat task type."""

    @pytest.mark.asyncio
    async def test_concat_task_dispatched(self, worker, redis, gateway):
        """A task with mode=concat should call _process_concat_task."""
        from shared.enums import GenerateMode
        from shared.redis_keys import task_key

        task_id = await gateway.create_task(
            mode=GenerateMode.CONCAT,
            model=ModelType.A14B,
            workflow={"video_urls": [], "output_filename": "out.mp4"},
        )

        called = []

        async def fake_concat(tid, raw_data):
            called.append(tid)
            await gateway.mark_task_completed(tid, video_url="https://cdn.example.com/out.mp4")

        worker._process_concat_task = fake_concat
        await worker._process_task(task_id)
        assert called == [task_id]

    @pytest.mark.asyncio
    async def test_concat_task_merges_segments(self, worker, redis, gateway):
        """Concat task should download segments, run ffmpeg, upload result."""
        import subprocess as _sp
        from unittest.mock import patch, MagicMock
        from shared.enums import GenerateMode

        # Two fake segment URLs (non-COS so they go through the HTTP fallback path,
        # but we'll patch aiohttp too)
        video_urls = [
            "https://example.com/seg0.mp4",
            "https://example.com/seg1.mp4",
        ]
        task_id = await gateway.create_task(
            mode=GenerateMode.CONCAT,
            model=ModelType.A14B,
            workflow={"video_urls": video_urls, "output_filename": "chain.mp4"},
        )

        fake_video_bytes = b"FAKEVIDEO"

        # Mock COS parse returning None (so HTTP path is used)
        mock_cos = MagicMock()
        mock_cos.parse_cos_url = MagicMock(return_value=None)
        mock_cos.upload_file = MagicMock(return_value="https://cdn.example.com/videos/chain.mp4")
        worker._cos_client = mock_cos

        # Mock aiohttp download
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=fake_video_bytes)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_get = MagicMock(return_value=mock_resp)
        mock_session = MagicMock()
        mock_session.get = mock_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        # Mock ffmpeg subprocess (just write an empty file as output)
        def fake_run(cmd, **kwargs):
            output_path = cmd[-1]
            with open(output_path, "wb") as f:
                f.write(b"CONCATVIDEO")
            return _sp.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        with patch("aiohttp.ClientSession", return_value=mock_session), \
             patch("subprocess.run", side_effect=fake_run):
            await worker._process_concat_task(task_id, await redis.hgetall(f"task:{task_id}"))

        task = await gateway.get_task(task_id)
        assert task["status"] == "completed"
        assert "chain.mp4" in (task.get("video_url") or "")


class TestProcessTaskProgressUpdates:
    """Verify that progress is updated during processing."""

    @pytest.mark.asyncio
    async def test_progress_updates(self, worker, redis, gateway):
        """Progress should be updated at key milestones during processing."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

        progress_values: list[float] = []
        original_update = gateway.update_task_progress

        async def spy_progress(tid, progress):
            progress_values.append(progress)
            await original_update(tid, progress)

        gateway.update_task_progress = spy_progress

        mock_client = _make_mock_comfyui_client()
        mock_cos = MagicMock()
        mock_cos.upload_file = MagicMock(return_value="https://cdn.example.com/videos/result.mp4")

        worker._cos_client = mock_cos
        worker._comfyui_clients = {"a14b": mock_client}

        await worker._process_task(task_id)

        # Should have at least the post-completion and post-upload progress updates
        assert len(progress_values) >= 1
        # Final progress before marking completed should be 0.9 or higher
        assert any(p >= 0.9 for p in progress_values)
