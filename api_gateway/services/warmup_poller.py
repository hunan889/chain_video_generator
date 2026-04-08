"""Idle-aware GPU/ComfyUI warmup poller.

Submits a tiny T2V task every ``warmup_interval_sec`` when no real
GPU activity has been observed for at least ``warmup_idle_threshold_sec``.
The warmup task goes through the normal Redis queue → GPU worker →
ComfyUI → COS pipeline so it exercises every layer that real tasks
touch, keeping models hot in:

1. OS page cache (model files on disk)
2. ComfyUI's in-process model state (avoids re-deserialising safetensors)
3. GPU memory (avoids cold PCIe transfers)

The warmup task is tagged with ``category="warmup"`` so it's hidden
from the user-facing history page (TaskStore.list_history filters it
out by default; ``?category=warmup`` can be passed to debug it).

The first warmup fires shortly after gateway startup (after a small
stagger) so a fresh deploy / restart immediately starts warming the
GPU stack instead of waiting a full interval. Subsequent warmups
are skipped whenever real tasks have been active recently — pure
idle is the only state that costs GPU time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from api_gateway.config import GatewayConfig
from api_gateway.services.task_store import TaskStore
from shared.enums import GenerateMode, ModelType
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)

# Stagger first run so the warmup poller doesn't collide with task_poller
# startup or the first user request.
_FIRST_RUN_DELAY_SEC = 30

# Boot mode: after a fresh deploy / restart, fire several warmups in
# quick succession so the workers can distribute them across all known
# ComfyUI instances and warm them in parallel — instead of waiting one
# full ``warmup_interval_sec`` between each cycle. After this many runs
# the poller settles into the steady-state interval.
_BOOT_BURST_COUNT = 5
_BOOT_BURST_GAP_SEC = 30

# Warmup workflow constants — kept aligned with Wan22 4n+1 / 16-pixel
# constraints. 480x480 / 5 frames / 5 steps is the minimum that exercises
# all relevant model nodes (T5, HIGH sampler, LOW sampler, VAE) without
# producing a meaningful video.
_WARMUP_WIDTH = 480
_WARMUP_HEIGHT = 480
_WARMUP_FRAMES = 5
_WARMUP_FPS = 16
_WARMUP_STEPS = 5
_WARMUP_PROMPT = "warmup ping"


class WarmupPoller:
    """Background coroutine that submits warmup tasks during idle periods."""

    def __init__(
        self,
        gateway: TaskGateway,
        redis,
        config: GatewayConfig,
        task_store: TaskStore,
    ) -> None:
        self.gateway = gateway
        self.redis = redis
        self.config = config
        self.task_store = task_store
        self._task: Optional[asyncio.Task] = None
        self._last_warmup_at: float = 0.0

    async def start(self) -> None:
        if not self.config.warmup_enabled:
            logger.info("WarmupPoller disabled via config")
            return
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "WarmupPoller started (interval=%ds, idle_threshold=%ds)",
            self.config.warmup_interval_sec,
            self.config.warmup_idle_threshold_sec,
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("WarmupPoller stopped")

    async def _loop(self) -> None:
        """Main loop — boot burst, then idle-aware periodic ping."""
        try:
            await asyncio.sleep(_FIRST_RUN_DELAY_SEC)
        except asyncio.CancelledError:
            return

        # Boot burst: fire several warmups in quick succession so workers
        # can distribute them across all ComfyUI instances and warm them
        # in parallel. Each warmup is still skipped if real activity is
        # observed in the meantime.
        for i in range(_BOOT_BURST_COUNT):
            try:
                await self._maybe_warmup()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("WarmupPoller boot-burst iteration failed")
            if i < _BOOT_BURST_COUNT - 1:
                try:
                    await asyncio.sleep(_BOOT_BURST_GAP_SEC)
                except asyncio.CancelledError:
                    return
        logger.info(
            "WarmupPoller boot burst complete (%d warmups), entering steady state",
            _BOOT_BURST_COUNT,
        )

        # Steady state: periodic idle-aware warmup at the configured interval.
        while True:
            try:
                await asyncio.sleep(self.config.warmup_interval_sec)
            except asyncio.CancelledError:
                return
            try:
                await self._maybe_warmup()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("WarmupPoller iteration failed (will retry)")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _maybe_warmup(self) -> None:
        if await self._has_recent_activity():
            logger.debug("WarmupPoller: skipping (real activity within idle threshold)")
            return
        await self._submit_warmup_task()

    async def _has_recent_activity(self) -> bool:
        """Return True if any GPU task was active within the idle threshold.

        Scans ``task:*`` Redis hashes and checks ``completed_at`` /
        ``created_at``. Synthetic warmup tasks are ignored so the poller
        doesn't keep itself alive in a loop.
        """
        now = time.time()
        threshold = self.config.warmup_idle_threshold_sec
        cursor = 0
        while True:
            try:
                cursor, keys = await self.redis.scan(
                    cursor, match="task:*", count=200,
                )
            except Exception:
                logger.warning("WarmupPoller scan failed", exc_info=True)
                return True  # fail-safe: assume active, skip warmup
            for key in keys:
                try:
                    raw = await self.redis.hgetall(key)
                except Exception:
                    continue
                if not raw:
                    continue
                # Skip synthetic warmups so we don't reset the idle clock
                if raw.get("category") == "warmup":
                    continue
                # Check the most recent timestamp on the task
                ts_str = raw.get("completed_at") or raw.get("created_at") or "0"
                try:
                    ts = float(ts_str)
                except (TypeError, ValueError):
                    continue
                if ts > 0 and (now - ts) < threshold:
                    return True
            if cursor == 0:
                break
        return False

    async def _submit_warmup_task(self) -> None:
        """Build a minimal a14b T2V workflow and enqueue it."""
        try:
            from shared.workflow_builder import build_workflow
            workflow = build_workflow(
                mode=GenerateMode.T2V,
                model=ModelType.A14B,
                prompt=_WARMUP_PROMPT,
                width=_WARMUP_WIDTH,
                height=_WARMUP_HEIGHT,
                num_frames=_WARMUP_FRAMES,
                fps=_WARMUP_FPS,
                steps=_WARMUP_STEPS,
                cfg=1.0,
                shift=5.0,
                model_preset="nsfw_v2",
            )
        except Exception:
            logger.exception("WarmupPoller: build_workflow failed, skipping cycle")
            return

        params = {
            "prompt": _WARMUP_PROMPT,
            "width": _WARMUP_WIDTH,
            "height": _WARMUP_HEIGHT,
            "num_frames": _WARMUP_FRAMES,
            "fps": _WARMUP_FPS,
            "steps": _WARMUP_STEPS,
            "cfg": 1.0,
            "shift": 5.0,
            "warmup": True,
        }

        try:
            task_id = await self.gateway.create_task(
                mode=GenerateMode.T2V,
                model=ModelType.A14B,
                workflow=workflow,
                params=params,
                category_override="warmup",
            )
        except Exception:
            logger.exception("WarmupPoller: create_task failed")
            return

        self._last_warmup_at = time.time()
        logger.info("WarmupPoller: enqueued warmup task %s", task_id)
