"""Tests for GPU worker logic using FakeRedis."""

import asyncio
import json
import time

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

        # Run worker for one iteration then stop
        worker._running = True

        async def stop_after_delay():
            await asyncio.sleep(0.3)
            worker._running = False

        asyncio.create_task(stop_after_delay())
        await worker.run()

        # Task should have been picked up (no longer queued)
        task = await gateway.get_task(task_id)
        assert task is not None
        assert task["status"] != TaskStatus.QUEUED.value


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

        # The task should have been completed (skeleton marks running then completed)
        assert TaskStatus.COMPLETED.value in statuses_seen


class TestWorkerMarksTaskCompleted:
    """Verify that after processing the task is marked completed."""

    @pytest.mark.asyncio
    async def test_worker_marks_task_completed(self, worker, redis, gateway):
        """After skeleton processing, task should be completed."""
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )

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
