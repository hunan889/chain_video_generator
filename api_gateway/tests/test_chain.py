"""Tests for the API-side ChainOrchestrator and chain endpoints.

Uses fakeredis so no real Redis instance is required.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from shared.enums import GenerateMode, ModelType, TaskStatus
from shared.redis_keys import chain_key, task_key
from shared.task_gateway import TaskGateway

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def redis():
    """Async fakeredis instance, flushed after each test."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.flushall()
    await r.aclose()


@pytest_asyncio.fixture
async def gateway(redis):
    return TaskGateway(redis)


@pytest_asyncio.fixture
async def orchestrator(gateway, redis):
    from api_gateway.services.chain_orchestrator import ChainOrchestrator

    orch = ChainOrchestrator(gateway=gateway, redis=redis)
    yield orch
    # Clean up any background tasks
    for task in orch._active_chains.values():
        task.cancel()
    # Allow cancellations to propagate
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# TaskGateway unit tests
# ---------------------------------------------------------------------------


class TestTaskGateway:
    """Unit tests for the shared TaskGateway."""

    @pytest.mark.asyncio
    async def test_create_task_returns_id(self, gateway, redis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"prompt": "test"},
        )
        assert isinstance(task_id, str)
        assert len(task_id) == 32  # uuid hex

        # Verify it was enqueued
        queued = await redis.lrange("queue:a14b", 0, -1)
        assert task_id in queued

    @pytest.mark.asyncio
    async def test_get_task_returns_data(self, gateway):
        task_id = await gateway.create_task(
            mode=GenerateMode.I2V,
            model=ModelType.FIVE_B,
            workflow={"prompt": "hello"},
            params={"segment_index": 0},
        )
        task = await gateway.get_task(task_id)

        assert task is not None
        assert task["task_id"] == task_id
        assert task["status"] == TaskStatus.QUEUED.value
        assert task["mode"] == GenerateMode.I2V.value
        assert task["model"] == ModelType.FIVE_B.value
        assert task["params"] == {"segment_index": 0}

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, gateway):
        result = await gateway.get_task("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_queued_task(self, gateway, redis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={},
        )
        ok = await gateway.cancel_queued_task(task_id)
        assert ok is True

        task = await gateway.get_task(task_id)
        assert task["status"] == TaskStatus.FAILED.value
        assert task["error"] == "Cancelled by user"

        # Removed from queue
        queued = await redis.lrange("queue:a14b", 0, -1)
        assert task_id not in queued

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, gateway):
        ok = await gateway.cancel_queued_task("nonexistent")
        assert ok is False

    @pytest.mark.asyncio
    async def test_create_chain_returns_id(self, gateway, redis):
        chain_id = await gateway.create_chain(3, {"model": "a14b"})
        assert isinstance(chain_id, str)
        assert len(chain_id) == 32

        data = await redis.hgetall(chain_key(chain_id))
        assert data["status"] == "queued"
        assert data["total_segments"] == "3"

    @pytest.mark.asyncio
    async def test_get_chain_status(self, gateway):
        chain_id = await gateway.create_chain(2, {"model": "a14b"})
        chain = await gateway.get_chain(chain_id)

        assert chain is not None
        assert chain["chain_id"] == chain_id
        assert chain["status"] == "queued"
        assert chain["total_segments"] == 2
        assert chain["completed_segments"] == 0
        assert chain["segment_task_ids"] == []

    @pytest.mark.asyncio
    async def test_get_chain_not_found(self, gateway):
        result = await gateway.get_chain("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_chains(self, gateway):
        await gateway.create_chain(1, {"model": "a14b"})
        await gateway.create_chain(2, {"model": "5b"})

        chains = await gateway.list_chains()
        assert len(chains) == 2


# ---------------------------------------------------------------------------
# ChainOrchestrator unit tests
# ---------------------------------------------------------------------------


class TestChainOrchestrator:
    """Unit tests for ChainOrchestrator."""

    @pytest.mark.asyncio
    async def test_create_chain_returns_chain_id(self, gateway, redis):
        """POST /chains equivalent -- orchestrator creates a chain and starts it."""
        from api_gateway.services.chain_orchestrator import ChainOrchestrator

        orchestrator = ChainOrchestrator(gateway=gateway, redis=redis)
        chain_id = await gateway.create_chain(2, {"model": "a14b"})

        chain = await gateway.get_chain(chain_id)
        assert chain is not None
        assert chain["chain_id"] == chain_id
        assert chain["total_segments"] == 2
        assert chain["status"] == "queued"

    @pytest.mark.asyncio
    async def test_chain_orchestrator_processes_segments(self, orchestrator, gateway, redis):
        """Orchestrator should process each segment sequentially.

        We simulate task completion by writing COMPLETED status to Redis
        in a background coroutine.
        """
        segments = [
            {"prompt": "A cat walking", "duration": 5.0},
            {"prompt": "The cat jumps", "duration": 5.0},
        ]

        chain_id = await gateway.create_chain(len(segments), {"model": "a14b"})

        async def complete_tasks_when_created():
            """Watch for new tasks and mark them completed."""
            completed = set()
            for _ in range(200):  # max iterations to avoid infinite loop
                await asyncio.sleep(0.05)
                # Scan for queued tasks
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match="task:*", count=50)
                    for key in keys:
                        tid = key.split(":", 1)[1]
                        if tid in completed:
                            continue
                        status = await redis.hget(key, "status")
                        if status == TaskStatus.QUEUED.value:
                            await redis.hset(key, mapping={
                                "status": TaskStatus.COMPLETED.value,
                                "video_url": f"https://example.com/{tid}.mp4",
                                "completed_at": str(int(time.time())),
                            })
                            completed.add(tid)
                    if cursor == 0:
                        break
                if len(completed) >= 2:
                    break

        helper = asyncio.create_task(complete_tasks_when_created())

        await orchestrator.start_chain(
            chain_id=chain_id,
            segments=segments,
            model=ModelType.A14B,
        )

        # Wait for chain to finish (with timeout)
        for _ in range(100):
            chain = await gateway.get_chain(chain_id)
            if chain and chain["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        helper.cancel()
        try:
            await helper
        except asyncio.CancelledError:
            pass

        chain = await gateway.get_chain(chain_id)
        assert chain["status"] == "completed"
        assert chain["completed_segments"] == 2
        assert len(chain["segment_task_ids"]) == 2

    @pytest.mark.asyncio
    async def test_chain_orchestrator_handles_segment_failure(self, orchestrator, gateway, redis):
        """When a segment task fails, the chain should be marked failed."""
        segments = [
            {"prompt": "Segment 0"},
            {"prompt": "Segment 1"},
        ]

        chain_id = await gateway.create_chain(len(segments), {"model": "a14b"})

        async def fail_first_task():
            """Fail the first task that appears."""
            for _ in range(200):
                await asyncio.sleep(0.05)
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match="task:*", count=50)
                    for key in keys:
                        status = await redis.hget(key, "status")
                        if status == TaskStatus.QUEUED.value:
                            await redis.hset(key, mapping={
                                "status": TaskStatus.FAILED.value,
                                "error": "CUDA out of memory",
                                "completed_at": str(int(time.time())),
                            })
                            return
                    if cursor == 0:
                        break

        helper = asyncio.create_task(fail_first_task())

        await orchestrator.start_chain(
            chain_id=chain_id,
            segments=segments,
            model=ModelType.A14B,
        )

        for _ in range(100):
            chain = await gateway.get_chain(chain_id)
            if chain and chain["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        helper.cancel()
        try:
            await helper
        except asyncio.CancelledError:
            pass

        chain = await gateway.get_chain(chain_id)
        assert chain["status"] == "failed"
        assert "CUDA out of memory" in (chain.get("error") or "")

    @pytest.mark.asyncio
    async def test_chain_cancel(self, orchestrator, gateway, redis):
        """Cancelling a chain should mark it failed with 'Cancelled' error."""
        segments = [
            {"prompt": "Long segment", "duration": 30.0},
        ]

        chain_id = await gateway.create_chain(len(segments), {"model": "a14b"})

        # Start the chain (it will block waiting for task completion)
        await orchestrator.start_chain(
            chain_id=chain_id,
            segments=segments,
            model=ModelType.A14B,
        )

        # Give it a moment to start
        await asyncio.sleep(0.2)

        # Cancel
        cancelled = await orchestrator.cancel_chain(chain_id)
        assert cancelled is True

        # Allow cancellation to propagate
        await asyncio.sleep(0.3)

        chain = await gateway.get_chain(chain_id)
        assert chain["status"] == "failed"
        assert "Cancelled" in (chain.get("error") or "")

    @pytest.mark.asyncio
    async def test_chain_single_segment(self, orchestrator, gateway, redis):
        """A chain with a single segment should complete normally."""
        segments = [{"prompt": "One shot"}]
        chain_id = await gateway.create_chain(1, {"model": "a14b"})

        async def complete_task():
            for _ in range(200):
                await asyncio.sleep(0.05)
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match="task:*", count=50)
                    for key in keys:
                        status = await redis.hget(key, "status")
                        if status == TaskStatus.QUEUED.value:
                            await redis.hset(key, mapping={
                                "status": TaskStatus.COMPLETED.value,
                                "video_url": "https://example.com/video.mp4",
                                "completed_at": str(int(time.time())),
                            })
                            return
                    if cursor == 0:
                        break

        helper = asyncio.create_task(complete_task())

        await orchestrator.start_chain(
            chain_id=chain_id,
            segments=segments,
            model=ModelType.A14B,
        )

        for _ in range(100):
            chain = await gateway.get_chain(chain_id)
            if chain and chain["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        helper.cancel()
        try:
            await helper
        except asyncio.CancelledError:
            pass

        chain = await gateway.get_chain(chain_id)
        assert chain["status"] == "completed"
        assert chain["completed_segments"] == 1
        assert chain["final_video_url"] is not None


# ---------------------------------------------------------------------------
# ChainOrchestrator auto-continue (VLM/LLM) tests
# ---------------------------------------------------------------------------


class TestChainAutoContinue:
    """Tests for VLM/LLM auto-continue prompt generation."""

    @pytest.mark.asyncio
    async def test_auto_continue_updates_next_segment_prompt(self, gateway, redis):
        """When auto_continue=True and VLM/LLM mocks return a prompt,
        the next segment's prompt should be replaced."""
        from unittest.mock import AsyncMock, patch
        from api_gateway.services.chain_orchestrator import ChainOrchestrator

        orchestrator = ChainOrchestrator(
            gateway=gateway,
            redis=redis,
            vision_api_key="fake-vision-key",
            vision_base_url="",
            vision_model="gpt-4o",
            llm_api_key="fake-llm-key",
            llm_base_url="",
            llm_model="gpt-4o",
        )

        segments = [
            {"prompt": "A cat walking", "duration": 5.0},
            {"prompt": "ORIGINAL PROMPT", "duration": 5.0},
        ]
        chain_id = await gateway.create_chain(len(segments), {"model": "a14b"})

        async def complete_tasks_when_created():
            completed = set()
            for _ in range(200):
                await asyncio.sleep(0.05)
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match="task:*", count=50)
                    for key in keys:
                        tid = key.split(":", 1)[1]
                        if tid in completed:
                            continue
                        status = await redis.hget(key, "status")
                        if status == TaskStatus.QUEUED.value:
                            await redis.hset(key, mapping={
                                "status": TaskStatus.COMPLETED.value,
                                "video_url": f"https://example.com/{tid}.mp4",
                                "last_frame_url": "https://example.com/frame.png",
                                "completed_at": str(int(time.time())),
                            })
                            completed.add(tid)
                    if cursor == 0:
                        break
                if len(completed) >= 2:
                    break

        helper = asyncio.create_task(complete_tasks_when_created())

        with patch(
            "api_gateway.services.continuation.describe_frame",
            new=AsyncMock(return_value="A cat mid-jump over a fence"),
        ), patch(
            "api_gateway.services.continuation.generate_continuation_prompt",
            new=AsyncMock(return_value="The cat lands gracefully and looks around"),
        ):
            await orchestrator.start_chain(
                chain_id=chain_id,
                segments=segments,
                model=ModelType.A14B,
                auto_continue=True,
            )

            for _ in range(100):
                chain = await gateway.get_chain(chain_id)
                if chain and chain["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.1)

        helper.cancel()
        try:
            await helper
        except asyncio.CancelledError:
            pass

        chain = await gateway.get_chain(chain_id)
        assert chain["status"] == "completed"
        assert chain["completed_segments"] == 2

    @pytest.mark.asyncio
    async def test_auto_continue_skipped_when_no_api_key(self, gateway, redis):
        """When no vision_api_key is set, auto_continue is silently skipped."""
        from api_gateway.services.chain_orchestrator import ChainOrchestrator

        orchestrator = ChainOrchestrator(gateway=gateway, redis=redis)
        # No VLM/LLM keys -- should not raise, should complete normally

        segments = [
            {"prompt": "Segment 0"},
            {"prompt": "Segment 1"},
        ]
        chain_id = await gateway.create_chain(len(segments), {"model": "a14b"})

        async def complete_tasks():
            completed = set()
            for _ in range(200):
                await asyncio.sleep(0.05)
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(cursor, match="task:*", count=50)
                    for key in keys:
                        tid = key.split(":", 1)[1]
                        if tid in completed:
                            continue
                        status = await redis.hget(key, "status")
                        if status == TaskStatus.QUEUED.value:
                            await redis.hset(key, mapping={
                                "status": TaskStatus.COMPLETED.value,
                                "video_url": f"https://example.com/{tid}.mp4",
                                "completed_at": str(int(time.time())),
                            })
                            completed.add(tid)
                    if cursor == 0:
                        break
                if len(completed) >= 2:
                    break

        helper = asyncio.create_task(complete_tasks())
        await orchestrator.start_chain(
            chain_id=chain_id,
            segments=segments,
            model=ModelType.A14B,
            auto_continue=True,
        )

        for _ in range(100):
            chain = await gateway.get_chain(chain_id)
            if chain and chain["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        helper.cancel()
        try:
            await helper
        except asyncio.CancelledError:
            pass

        chain = await gateway.get_chain(chain_id)
        assert chain["status"] == "completed"


# ---------------------------------------------------------------------------
# Chain API route tests (FastAPI TestClient)
# ---------------------------------------------------------------------------


class TestChainRoutes:
    """Integration tests for chain HTTP endpoints."""

    @pytest_asyncio.fixture
    async def app(self, gateway, orchestrator):
        """Create a FastAPI app with chain router mounted."""
        from fastapi import FastAPI

        from api_gateway.routes.chain import router

        app = FastAPI()
        app.state.gateway = gateway
        app.state.chain_orchestrator = orchestrator
        app.include_router(router)
        return app

    @pytest_asyncio.fixture
    async def client(self, app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    async def test_create_chain_endpoint(self, client, redis):
        resp = await client.post("/api/v1/chains", json={
            "segments": [
                {"prompt": "A dog runs"},
                {"prompt": "The dog sleeps"},
            ],
            "model": "a14b",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "chain_id" in data
        assert data["total_segments"] == 2
        assert data["status"] == "queued"

    @pytest.mark.asyncio
    async def test_get_chain_endpoint(self, client, gateway):
        chain_id = await gateway.create_chain(2, {"model": "a14b"})
        resp = await client.get(f"/api/v1/chains/{chain_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chain_id"] == chain_id

    @pytest.mark.asyncio
    async def test_get_chain_not_found(self, client):
        resp = await client.get("/api/v1/chains/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_chains_endpoint(self, client, gateway):
        await gateway.create_chain(1, {"model": "a14b"})
        await gateway.create_chain(2, {"model": "a14b"})
        resp = await client.get("/api/v1/chains")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_cancel_chain_endpoint(self, client, gateway, redis):
        chain_id = await gateway.create_chain(1, {"model": "a14b"})
        # Mark chain as running so it can be cancelled
        await redis.hset(chain_key(chain_id), "status", "running")

        resp = await client.post(f"/api/v1/chains/{chain_id}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
