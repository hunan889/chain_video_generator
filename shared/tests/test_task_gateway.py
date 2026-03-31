"""Tests for shared.task_gateway — write FIRST, implement after.

Uses a fake Redis (dict-backed) to test pure data operations without network.
"""

import json
import time

import pytest

from shared.enums import GenerateMode, ModelType, TaskStatus
from shared.task_gateway import TaskGateway


# ============================================================
# Fake Redis for testing (async dict-backed)
# ============================================================


class FakeRedis:
    """Minimal async Redis fake for hash/list/scan ops."""

    def __init__(self):
        self._data: dict[str, dict | list | set] = {}
        self._expiry: dict[str, int] = {}

    async def ping(self) -> bool:
        return True

    async def hset(self, key: str, mapping: dict = None, **kwargs):
        if key not in self._data or not isinstance(self._data[key], dict):
            self._data[key] = {}
        if mapping:
            self._data[key].update(mapping)
        self._data[key].update(kwargs)

    async def hgetall(self, key: str) -> dict:
        val = self._data.get(key, {})
        return val if isinstance(val, dict) else {}

    async def hget(self, key: str, field: str) -> str | None:
        val = self._data.get(key, {})
        if isinstance(val, dict):
            return val.get(field)
        return None

    async def expire(self, key: str, seconds: int):
        self._expiry[key] = seconds

    async def rpush(self, key: str, *values):
        if key not in self._data or not isinstance(self._data[key], list):
            self._data[key] = []
        self._data[key].extend(values)

    async def lrem(self, key: str, count: int, value: str):
        if key in self._data and isinstance(self._data[key], list):
            self._data[key] = [v for v in self._data[key] if v != value]

    async def llen(self, key: str) -> int:
        val = self._data.get(key, [])
        return len(val) if isinstance(val, list) else 0

    async def scan(self, cursor: int, match: str = "*", count: int = 100):
        pattern = match.replace("*", "")
        keys = [k for k in self._data if k.startswith(pattern)]
        return 0, keys


# ============================================================
# Tests
# ============================================================


@pytest.fixture()
def redis():
    return FakeRedis()


@pytest.fixture()
def gateway(redis):
    gw = TaskGateway(redis=redis, task_expiry=86400)
    return gw


class TestRedisAlive:
    @pytest.mark.asyncio
    async def test_ping_succeeds(self, gateway: TaskGateway):
        assert await gateway.redis_alive() is True


class TestCreateTask:
    @pytest.mark.asyncio
    async def test_returns_task_id(self, gateway: TaskGateway):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"nodes": []},
        )
        assert isinstance(task_id, str)
        assert len(task_id) == 32  # hex uuid

    @pytest.mark.asyncio
    async def test_task_stored_in_redis(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.I2V,
            model=ModelType.FIVE_B,
            workflow={"test": True},
            params={"prompt": "hello"},
        )
        data = await redis.hgetall(f"task:{task_id}")
        assert data["status"] == "queued"
        assert data["mode"] == "i2v"
        assert data["model"] == "5b"
        assert json.loads(data["workflow"]) == {"test": True}
        assert json.loads(data["params"]) == {"prompt": "hello"}

    @pytest.mark.asyncio
    async def test_task_queued(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={},
        )
        queue = redis._data.get("queue:a14b", [])
        assert task_id in queue

    @pytest.mark.asyncio
    async def test_chain_id_stored(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={},
            chain_id="chain_abc",
        )
        data = await redis.hgetall(f"task:{task_id}")
        assert data["chain_id"] == "chain_abc"

    @pytest.mark.asyncio
    async def test_expiry_set(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={},
        )
        assert redis._expiry.get(f"task:{task_id}") == 86400


class TestGetTask:
    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, gateway: TaskGateway):
        assert await gateway.get_task("nonexistent") is None

    @pytest.mark.asyncio
    async def test_returns_task_data(self, gateway: TaskGateway):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={"x": 1},
            params={"prompt": "test"},
        )
        task = await gateway.get_task(task_id)
        assert task is not None
        assert task["task_id"] == task_id
        assert task["status"] == "queued"
        assert task["mode"] == "t2v"
        assert task["model"] == "a14b"
        assert task["progress"] == 0.0
        assert task["params"] == {"prompt": "test"}
        assert task["created_at"] is not None


class TestCancelTask:
    @pytest.mark.asyncio
    async def test_cancel_queued_task(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={},
        )
        result = await gateway.cancel_queued_task(task_id)
        assert result is True
        data = await redis.hgetall(f"task:{task_id}")
        assert data["status"] == "failed"
        assert data["error"] == "Cancelled by user"

    @pytest.mark.asyncio
    async def test_cancel_removes_from_queue(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={},
        )
        await gateway.cancel_queued_task(task_id)
        queue = redis._data.get("queue:a14b", [])
        assert task_id not in queue

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_false(self, gateway: TaskGateway):
        result = await gateway.cancel_queued_task("nope")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_completed_returns_false(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V,
            model=ModelType.A14B,
            workflow={},
        )
        await redis.hset(f"task:{task_id}", mapping={"status": "completed"})
        result = await gateway.cancel_queued_task(task_id)
        assert result is False


class TestListTasks:
    @pytest.mark.asyncio
    async def test_empty(self, gateway: TaskGateway):
        assert await gateway.list_tasks() == []

    @pytest.mark.asyncio
    async def test_excludes_chain_tasks(self, gateway: TaskGateway):
        await gateway.create_task(
            mode=GenerateMode.T2V, model=ModelType.A14B,
            workflow={}, chain_id="c1",
        )
        await gateway.create_task(
            mode=GenerateMode.T2V, model=ModelType.A14B,
            workflow={},
        )
        tasks = await gateway.list_tasks()
        assert len(tasks) == 1
        assert "chain_id" not in tasks[0] or tasks[0].get("chain_id") is None

    @pytest.mark.asyncio
    async def test_sorted_by_priority(self, gateway: TaskGateway, redis: FakeRedis):
        t1 = await gateway.create_task(
            mode=GenerateMode.T2V, model=ModelType.A14B, workflow={},
        )
        t2 = await gateway.create_task(
            mode=GenerateMode.T2V, model=ModelType.A14B, workflow={},
        )
        await redis.hset(f"task:{t1}", mapping={"status": "completed"})
        # t2 is still queued, should come first
        tasks = await gateway.list_tasks()
        assert tasks[0]["task_id"] == t2


class TestCreateChain:
    @pytest.mark.asyncio
    async def test_returns_chain_id(self, gateway: TaskGateway):
        chain_id = await gateway.create_chain(3, {"prompt": "test"})
        assert isinstance(chain_id, str)
        assert len(chain_id) == 32

    @pytest.mark.asyncio
    async def test_chain_stored(self, gateway: TaskGateway, redis: FakeRedis):
        chain_id = await gateway.create_chain(5, {"mode": "story"})
        data = await redis.hgetall(f"chain:{chain_id}")
        assert data["status"] == "queued"
        assert data["total_segments"] == "5"
        assert data["completed_segments"] == "0"


class TestGetChain:
    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, gateway: TaskGateway):
        assert await gateway.get_chain("nope") is None

    @pytest.mark.asyncio
    async def test_returns_chain_data(self, gateway: TaskGateway):
        chain_id = await gateway.create_chain(2, {"p": "val"})
        chain = await gateway.get_chain(chain_id)
        assert chain["chain_id"] == chain_id
        assert chain["total_segments"] == 2
        assert chain["status"] == "queued"


class TestListChains:
    @pytest.mark.asyncio
    async def test_empty(self, gateway: TaskGateway):
        assert await gateway.list_chains() == []

    @pytest.mark.asyncio
    async def test_lists_chains(self, gateway: TaskGateway):
        await gateway.create_chain(2, {})
        await gateway.create_chain(3, {})
        chains = await gateway.list_chains()
        assert len(chains) == 2


class TestMarkTaskStatus:
    @pytest.mark.asyncio
    async def test_mark_running(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V, model=ModelType.A14B, workflow={},
        )
        await gateway.mark_task_running(task_id, comfyui_url="http://localhost:8188", prompt_id="p123")
        data = await redis.hgetall(f"task:{task_id}")
        assert data["status"] == "running"
        assert data["comfyui_url"] == "http://localhost:8188"
        assert data["prompt_id"] == "p123"

    @pytest.mark.asyncio
    async def test_mark_completed(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V, model=ModelType.A14B, workflow={},
        )
        await gateway.mark_task_completed(task_id, video_url="https://cdn/v.mp4", last_frame_url="https://cdn/f.png")
        data = await redis.hgetall(f"task:{task_id}")
        assert data["status"] == "completed"
        assert data["video_url"] == "https://cdn/v.mp4"
        assert data["last_frame_url"] == "https://cdn/f.png"

    @pytest.mark.asyncio
    async def test_mark_failed(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V, model=ModelType.A14B, workflow={},
        )
        await gateway.mark_task_failed(task_id, error="OOM")
        data = await redis.hgetall(f"task:{task_id}")
        assert data["status"] == "failed"
        assert data["error"] == "OOM"

    @pytest.mark.asyncio
    async def test_update_progress(self, gateway: TaskGateway, redis: FakeRedis):
        task_id = await gateway.create_task(
            mode=GenerateMode.T2V, model=ModelType.A14B, workflow={},
        )
        await gateway.update_task_progress(task_id, 0.75)
        data = await redis.hgetall(f"task:{task_id}")
        assert data["progress"] == "0.75"
