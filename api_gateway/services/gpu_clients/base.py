"""Common submit-and-poll pattern shared by all gpu_clients.

Both faceswap (via gpu/comfyui_worker) and inference (via gpu/inference_worker)
use the same Redis-based protocol:

    1. Gateway writes a HASH at ``task:<id>`` with the request payload.
    2. Gateway RPUSH the task_id onto a queue list.
    3. Worker BLPOPs the queue, processes, and writes back ``status``,
       ``result`` / ``error`` fields on the same HASH.
    4. Gateway polls ``task:<id>`` for completion.

This module factors that pattern out so individual clients only need to
specify which queue + how to encode/decode their payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from shared.redis_keys import task_key

logger = logging.getLogger(__name__)


class GpuClientError(RuntimeError):
    """Generic failure surfaced to callers (worker reported failed)."""


class GpuClientTimeout(TimeoutError):
    """Worker did not respond within the configured deadline."""


async def submit_and_wait(
    redis,
    *,
    queue_name: str,
    task_data: dict,
    timeout: float,
    poll_interval: float = 0.2,
    task_ttl: int = 300,
    error_class: type[GpuClientError] = GpuClientError,
    timeout_class: type[GpuClientTimeout] = GpuClientTimeout,
) -> dict:
    """Submit a task hash to a Redis queue and poll until completion.

    Args:
        redis: An async redis client (``redis.asyncio.Redis``).
        queue_name: Full Redis LIST key, e.g. ``"queue:inference"``.
        task_data: Mapping written to ``task:<id>`` (must include ``mode``).
                   ``status``, ``created_at`` are filled in if missing.
        timeout: Total seconds to wait for ``status == "completed"``.
        poll_interval: Seconds between Redis HGETALL polls.
        task_ttl: TTL applied to the ``task:<id>`` hash.
        error_class: Exception type to raise when worker reports ``failed``.
        timeout_class: Exception type to raise when deadline elapses.

    Returns:
        The full Redis hash dict (already decoded) of the completed task.
        Caller is responsible for parsing any ``result`` payload.

    Raises:
        error_class: If worker writes ``status == "failed"``.
        timeout_class: If poll deadline elapses without completion.
    """
    if redis is None:
        raise error_class("no redis connection available")

    task_id = uuid.uuid4().hex
    tk = task_key(task_id)

    payload = dict(task_data)
    payload.setdefault("status", "queued")
    payload.setdefault("created_at", str(int(time.time())))

    await redis.hset(tk, mapping=payload)
    await redis.expire(tk, task_ttl)
    await redis.rpush(queue_name, task_id)

    logger.debug(
        "submit_and_wait: queued task %s on %s (mode=%s)",
        task_id, queue_name, payload.get("mode", "?"),
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(poll_interval)
        raw = await redis.hgetall(tk)
        status = (raw or {}).get("status", "")
        if status == "completed":
            return raw
        if status == "failed":
            err = raw.get("error", "unknown error")
            raise error_class(f"task {task_id} failed: {err}")

    raise timeout_class(
        f"task {task_id} on {queue_name} timed out after {timeout:.1f}s"
    )


def decode_result_field(raw_hash: dict, key: str = "result") -> Any:
    """Decode a JSON-encoded field from a redis hash, tolerant of empty/None."""
    val = (raw_hash or {}).get(key)
    if val in (None, "", b"", b"null"):
        return None
    if isinstance(val, bytes):
        val = val.decode("utf-8", errors="replace")
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val
