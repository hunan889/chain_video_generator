"""Heartbeat reporter -- periodically updates worker liveness in Redis."""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from shared.redis_keys import worker_heartbeat_key, worker_loras_key

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from gpu.comfyui_worker.config import WorkerConfig

logger = logging.getLogger(__name__)


class HeartbeatReporter:
    """Periodically reports worker liveness to Redis.

    Writes a HASH at ``worker:heartbeat:<worker_id>`` containing:
    - last_seen:  unix timestamp of the last heartbeat
    - model_keys: JSON list of model keys this worker handles
    - status:     "idle" | "busy"
    - instances:  JSON dict of instance health per model (if pool attached)
    """

    def __init__(self, redis: "Redis", config: "WorkerConfig") -> None:
        self._redis = redis
        self._config = config
        self._status: str = "idle"
        self._task: asyncio.Task | None = None
        self._beat_count: int = 0
        self._instance_pool = None  # set by worker after pool init

    def set_instance_pool(self, pool) -> None:
        """Attach the InstancePool for health reporting."""
        self._instance_pool = pool

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
        while True:
            try:
                await self._send_heartbeat(hb_key)
                # Publish LoRA list on first beat and every 10 beats
                if self._beat_count % 10 == 0:
                    await self._publish_loras()
                self._beat_count += 1
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Heartbeat loop error for %s (will retry)", self._config.worker_id)
            await asyncio.sleep(self._config.heartbeat_interval)

    async def _send_heartbeat(self, hb_key: str) -> None:
        """Write one heartbeat to Redis, including GPU stats from ComfyUI."""
        try:
            mapping = {
                "last_seen": str(int(time.time())),
                "model_keys": json.dumps(self._config.model_keys),
                "status": self._status,
            }

            # Fetch GPU stats from ComfyUI /system_stats
            try:
                import aiohttp
                first_url = next(iter(self._config.comfyui_urls.values()), "")
                if first_url:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f"{first_url}/system_stats", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                            if resp.status == 200:
                                stats = await resp.json()
                                devices = stats.get("devices", [])
                                if devices:
                                    dev = devices[0]
                                    mapping["device_name"] = dev.get("name", "")
                                    mapping["device_type"] = dev.get("type", "")
                                    mapping["vram_total_mb"] = str(dev.get("vram_total", 0) // 1024 // 1024)
                                    mapping["vram_free_mb"] = str(dev.get("vram_free", 0) // 1024 // 1024)
                                    mapping["vram_used_mb"] = str((dev.get("vram_total", 0) - dev.get("vram_free", 0)) // 1024 // 1024)
                                    mapping["torch_vram_total_mb"] = str(dev.get("torch_vram_total", 0) // 1024 // 1024)
                                    mapping["torch_vram_used_mb"] = str((dev.get("torch_vram_total", 0) - dev.get("torch_vram_free", 0)) // 1024 // 1024)
                                mapping["comfyui_url"] = first_url
                        # Also check if ComfyUI has any running prompt (even external ones)
                        async with session.get(f"{first_url}/queue", timeout=aiohttp.ClientTimeout(total=3)) as qresp:
                            if qresp.status == 200:
                                qdata = await qresp.json()
                                running = qdata.get("queue_running", [])
                                pending = qdata.get("queue_pending", [])
                                if running:
                                    mapping["status"] = "busy"
                                    mapping["comfyui_running"] = str(len(running))
                                    mapping["comfyui_pending"] = str(len(pending))
            except Exception:
                pass  # GPU stats are best-effort

            # Include instance pool health summary
            if self._instance_pool is not None:
                try:
                    mapping["instances"] = json.dumps(
                        self._instance_pool.get_health_summary()
                    )
                except Exception:
                    pass

            await self._redis.hset(hb_key, mapping=mapping)
        except Exception:
            logger.exception("Failed to send heartbeat for %s", self._config.worker_id)

    async def _publish_loras(self) -> None:
        """Scan LORAS_DIR and publish the list to Redis."""
        loras_dir = self._config.loras_dir
        if not loras_dir:
            return
        loras_path = Path(loras_dir)
        if not loras_path.is_dir():
            return
        try:
            loras = []
            for f in sorted(loras_path.glob("*.safetensors")):
                size_mb = round(f.stat().st_size / (1024 * 1024), 1)
                loras.append({"name": f.stem, "filename": f.name, "size_mb": size_mb})
            loras_key = worker_loras_key(self._config.worker_id)
            await self._redis.set(loras_key, json.dumps(loras))
            logger.debug("Published %d LoRAs for worker %s", len(loras), self._config.worker_id)
        except Exception:
            logger.exception("Failed to publish LoRAs for %s", self._config.worker_id)
