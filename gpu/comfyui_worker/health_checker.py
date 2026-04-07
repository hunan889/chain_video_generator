"""Background health checker for ComfyUI instances.

Runs as an asyncio task alongside the worker main loop.
Probes all known instances periodically and updates the InstancePool.
"""

import asyncio
import logging

from gpu.comfyui_worker.comfyui_client import ComfyUIClient
from gpu.comfyui_worker.instance_pool import InstancePool

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 15       # probe every 15 seconds
REGISTRY_INTERVAL = 2       # refresh from Redis every N cycles (30s at default)


class HealthChecker:
    """Probes ComfyUI instances and updates pool health state."""

    def __init__(
        self,
        pool: InstancePool,
        interval: float = DEFAULT_INTERVAL,
    ):
        self._pool = pool
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._cycle = 0
        # Cache ComfyUI clients for health probes (separate from worker clients)
        self._probe_clients: dict[str, ComfyUIClient] = {}

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._loop())
        logger.info("HealthChecker started (interval=%ds)", self._interval)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def _get_probe_client(self, url: str) -> ComfyUIClient:
        if url not in self._probe_clients:
            self._probe_clients[url] = ComfyUIClient(url)
        return self._probe_clients[url]

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                self._cycle += 1

                # Refresh from Redis registry every N cycles
                if self._cycle % REGISTRY_INTERVAL == 0:
                    await self._pool.refresh_from_registry()

                # Probe all instances
                await self._probe_all()
        except asyncio.CancelledError:
            logger.info("HealthChecker stopped")
        except Exception:
            logger.exception("HealthChecker crashed")

    async def _probe_all(self) -> None:
        all_instances = self._pool.get_all_instances()
        for model_key, instances in all_instances.items():
            for inst in instances:
                client = self._get_probe_client(inst.url)
                try:
                    alive = await client.is_alive()
                except Exception:
                    alive = False

                if alive:
                    self._pool.mark_healthy(model_key, inst.url)
                else:
                    self._pool.mark_unhealthy(model_key, inst.url)

    async def close(self) -> None:
        self.stop()
        for client in self._probe_clients.values():
            await client.close()
        self._probe_clients.clear()
