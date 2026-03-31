"""Heartbeat reporter -- periodically updates worker liveness in Redis."""

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

from shared.redis_keys import worker_heartbeat_key

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from gpu_worker.config import WorkerConfig

logger = logging.getLogger(__name__)


class HeartbeatReporter:
    """Periodically reports worker liveness to Redis.

    Writes a HASH at ``worker:heartbeat:<worker_id>`` containing:
    - last_seen:  unix timestamp of the last heartbeat
    - model_keys: JSON list of model keys this worker handles
    - status:     "idle" | "busy"
    """

    def __init__(self, redis: "Redis", config: "WorkerConfig") -> None:
        self._redis = redis
        self._config = config
        self._status: str = "idle"
        self._task: asyncio.Task | None = None

    def set_status(self, status: str) -> None:
        """Update the reported status (idle / busy)."""
        self._status = status

    async def start(self) -> None:
        """Start the background heartbeat loop."""
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            "Heartbeat started for worker %s (interval=%ds)",
            self._config.worker_id,
            self._config.heartbeat_interval,
        )

    async def stop(self) -> None:
        """Cancel the heartbeat loop and send a final offline beat."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Heartbeat stopped for worker %s", self._config.worker_id)

    async def _heartbeat_loop(self) -> None:
        """Send heartbeats at the configured interval."""
        hb_key = worker_heartbeat_key(self._config.worker_id)
        try:
            while True:
                await self._send_heartbeat(hb_key)
                await asyncio.sleep(self._config.heartbeat_interval)
        except asyncio.CancelledError:
            return

    async def _send_heartbeat(self, hb_key: str) -> None:
        """Write one heartbeat to Redis."""
        try:
            await self._redis.hset(
                hb_key,
                mapping={
                    "last_seen": str(int(time.time())),
                    "model_keys": json.dumps(self._config.model_keys),
                    "status": self._status,
                },
            )
        except Exception:
            logger.exception("Failed to send heartbeat for %s", self._config.worker_id)
