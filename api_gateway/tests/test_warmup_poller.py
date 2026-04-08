"""Unit tests for api_gateway.services.warmup_poller.

Mocks Redis + TaskGateway + workflow_builder so the test runs without
network or ffmpeg/ComfyUI access.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_gateway.services import warmup_poller as wp


def _make_config(*, enabled: bool = True, interval: int = 480, idle: int = 480):
    cfg = MagicMock()
    cfg.warmup_enabled = enabled
    cfg.warmup_interval_sec = interval
    cfg.warmup_idle_threshold_sec = idle
    return cfg


def _make_redis(task_hashes: list[dict] | None = None):
    """Build an AsyncMock Redis where SCAN returns one batch of task keys
    and HGETALL returns the supplied dicts in order."""
    redis = AsyncMock()
    keys = [f"task:t{i}" for i in range(len(task_hashes or []))]
    redis.scan = AsyncMock(return_value=(0, keys))

    iter_hashes = iter(task_hashes or [])
    async def _hgetall(key):
        try:
            return next(iter_hashes)
        except StopIteration:
            return {}
    redis.hgetall = AsyncMock(side_effect=_hgetall)
    return redis


@pytest.mark.asyncio
async def test_disabled_via_config():
    """When warmup_enabled=False, start() must not spawn the loop."""
    poller = wp.WarmupPoller(
        gateway=AsyncMock(), redis=AsyncMock(),
        config=_make_config(enabled=False), task_store=AsyncMock(),
    )
    await poller.start()
    assert poller._task is None


@pytest.mark.asyncio
async def test_has_recent_activity_true_for_recent_task():
    import time as _time
    redis = _make_redis([
        {"category": "local", "completed_at": str(_time.time() - 60)},
    ])
    poller = wp.WarmupPoller(
        gateway=AsyncMock(), redis=redis,
        config=_make_config(idle=480), task_store=AsyncMock(),
    )
    assert await poller._has_recent_activity() is True


@pytest.mark.asyncio
async def test_has_recent_activity_false_for_old_task():
    import time as _time
    redis = _make_redis([
        {"category": "local", "completed_at": str(_time.time() - 1000)},
    ])
    poller = wp.WarmupPoller(
        gateway=AsyncMock(), redis=redis,
        config=_make_config(idle=480), task_store=AsyncMock(),
    )
    assert await poller._has_recent_activity() is False


@pytest.mark.asyncio
async def test_warmup_tasks_dont_keep_themselves_alive():
    """Synthetic warmup tasks must NOT count as recent activity, otherwise
    the poller would keep itself alive forever after the first warmup."""
    import time as _time
    redis = _make_redis([
        {"category": "warmup", "completed_at": str(_time.time() - 10)},
    ])
    poller = wp.WarmupPoller(
        gateway=AsyncMock(), redis=redis,
        config=_make_config(idle=480), task_store=AsyncMock(),
    )
    assert await poller._has_recent_activity() is False


@pytest.mark.asyncio
async def test_submit_warmup_uses_category_override():
    gateway = AsyncMock()
    gateway.create_task = AsyncMock(return_value="warm_task_123")

    poller = wp.WarmupPoller(
        gateway=gateway, redis=AsyncMock(),
        config=_make_config(), task_store=AsyncMock(),
    )

    fake_workflow = {"_meta": {"version": "test"}}
    with patch("shared.workflow_builder.build_workflow", return_value=fake_workflow):
        await poller._submit_warmup_task()

    gateway.create_task.assert_called_once()
    call_kwargs = gateway.create_task.call_args.kwargs
    assert call_kwargs["category_override"] == "warmup"
    assert call_kwargs["params"]["warmup"] is True
    assert poller._last_warmup_at > 0


@pytest.mark.asyncio
async def test_maybe_warmup_skips_when_active():
    """If recent activity is detected, no warmup task should be submitted."""
    import time as _time
    redis = _make_redis([
        {"category": "local", "completed_at": str(_time.time() - 30)},
    ])
    gateway = AsyncMock()
    gateway.create_task = AsyncMock()

    poller = wp.WarmupPoller(
        gateway=gateway, redis=redis,
        config=_make_config(idle=480), task_store=AsyncMock(),
    )
    await poller._maybe_warmup()
    gateway.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_submit_warmup_handles_build_workflow_failure():
    """If build_workflow raises, _submit_warmup_task swallows the error."""
    gateway = AsyncMock()
    gateway.create_task = AsyncMock()

    poller = wp.WarmupPoller(
        gateway=gateway, redis=AsyncMock(),
        config=_make_config(), task_store=AsyncMock(),
    )

    with patch("shared.workflow_builder.build_workflow",
               side_effect=RuntimeError("template missing")):
        await poller._submit_warmup_task()

    gateway.create_task.assert_not_called()
