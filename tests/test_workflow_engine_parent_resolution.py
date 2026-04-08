"""Unit tests for WorkflowEngine._resolve_parent_workflow.

Covers the two-tier Redis → MySQL fallback introduced to support ClothOff
task IDs as valid continuation parents.
"""
import pytest
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Minimal stub so we can instantiate WorkflowEngine without real dependencies
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Minimal async Redis stub."""

    def __init__(self, store: dict | None = None):
        self._store: dict[str, dict] = store or {}

    async def hgetall(self, key: str) -> dict:
        return self._store.get(key, {})

    async def get(self, key: str):
        return None

    async def hset(self, *args, **kwargs):
        pass

    async def expire(self, *args, **kwargs):
        pass


class _FakeTaskStore:
    """Minimal async TaskStore stub."""

    def __init__(self, rows: dict | None = None):
        self._rows: dict[str, dict | None] = rows or {}

    async def get(self, task_id: str) -> dict | None:
        return self._rows.get(task_id)


def _make_engine(redis: _FakeRedis, task_store: _FakeTaskStore | None = None):
    """Construct a WorkflowEngine with fake dependencies."""
    from api_gateway.services.workflow_engine import WorkflowEngine

    engine = WorkflowEngine.__new__(WorkflowEngine)
    engine.redis = redis
    engine.task_store = task_store
    engine.gateway = MagicMock()
    engine.config = MagicMock()
    engine.cos_client = MagicMock()
    engine._active_tasks = {}
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolveParentWorkflow:

    @pytest.mark.asyncio
    async def test_redis_hit_returns_redis_row(self):
        """GPU-native workflow ID found in Redis → returned immediately."""
        redis_data = {
            "workflow:wf_abc123": {
                "status": "completed",
                "final_video_url": "https://cos.example/video.mp4",
            }
        }
        engine = _make_engine(_FakeRedis(redis_data))

        result = await engine._resolve_parent_workflow("wf_abc123")

        assert result is not None
        assert result["status"] == "completed"
        assert result["final_video_url"] == "https://cos.example/video.mp4"

    @pytest.mark.asyncio
    async def test_redis_miss_falls_back_to_task_store(self):
        """ClothOff bare-UUID not in Redis → falls back to TaskStore."""
        clothoff_id = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        task_store_rows = {
            clothoff_id: {
                "status": "completed",
                "final_video_url": "https://cos.example/clothoff_video.mp4",
            }
        }
        engine = _make_engine(_FakeRedis(), _FakeTaskStore(task_store_rows))

        result = await engine._resolve_parent_workflow(clothoff_id)

        assert result is not None
        assert result["status"] == "completed"
        assert result["final_video_url"] == "https://cos.example/clothoff_video.mp4"

    @pytest.mark.asyncio
    async def test_redis_miss_no_task_store_returns_none(self):
        """Redis miss and task_store is None → returns None (no crash)."""
        engine = _make_engine(_FakeRedis(), task_store=None)

        result = await engine._resolve_parent_workflow("wf_unknown")

        assert result is None

    @pytest.mark.asyncio
    async def test_both_miss_returns_none(self):
        """Unknown ID in both Redis and TaskStore → returns None."""
        engine = _make_engine(_FakeRedis(), _FakeTaskStore())

        result = await engine._resolve_parent_workflow("totally_unknown_id")

        assert result is None

    @pytest.mark.asyncio
    async def test_redis_hit_does_not_call_task_store(self):
        """Confirm short-circuit: TaskStore is never queried on Redis hit."""
        redis_data = {
            "workflow:wf_short": {
                "status": "completed",
                "final_video_url": "https://example.com/v.mp4",
            }
        }
        task_store = _FakeTaskStore()
        task_store.get = AsyncMock(wraps=task_store.get)

        engine = _make_engine(_FakeRedis(redis_data), task_store)
        await engine._resolve_parent_workflow("wf_short")

        task_store.get.assert_not_called()
