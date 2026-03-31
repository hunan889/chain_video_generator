"""GPU Worker -- polls Redis for tasks and executes ComfyUI workflows."""

import logging
from typing import TYPE_CHECKING

from shared.enums import TaskStatus
from shared.redis_keys import queue_key, task_key

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from gpu_worker.config import WorkerConfig
    from gpu_worker.heartbeat import HeartbeatReporter
    from shared.cos import COSClient
    from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)


class GPUWorker:
    """Polls Redis for tasks and executes ComfyUI workflows.

    This is a Phase-3 skeleton. The actual ComfyUI interaction (steps 3-7)
    will be migrated from ``api.services.task_manager`` in a later phase.
    """

    def __init__(
        self,
        config: "WorkerConfig",
        redis: "Redis",
        gateway: "TaskGateway",
        cos_client: "COSClient",
        heartbeat: "HeartbeatReporter | None" = None,
    ) -> None:
        self._config = config
        self._redis = redis
        self._gateway = gateway
        self._cos_client = cos_client
        self._heartbeat = heartbeat
        self._running: bool = False

    @property
    def model_keys(self) -> list[str]:
        return self._config.model_keys

    async def run(self) -> None:
        """Main loop: BLPOP from model queues, process, repeat."""
        queue_names = [queue_key(mk) for mk in self.model_keys]
        if not queue_names:
            logger.warning("No model keys configured -- worker has nothing to poll")
            return

        logger.info(
            "Worker %s started, polling queues: %s",
            self._config.worker_id,
            queue_names,
        )

        while self._running:
            result = await self._redis.blpop(queue_names, timeout=1)
            if result is None:
                continue

            _queue_name, task_id = result
            logger.info("Dequeued task %s", task_id)

            if self._heartbeat is not None:
                self._heartbeat.set_status("busy")

            try:
                await self._process_task(task_id)
            except Exception as exc:
                logger.exception("Task %s failed: %s", task_id, exc)
                await self._gateway.mark_task_failed(task_id, error=str(exc))
            finally:
                if self._heartbeat is not None:
                    self._heartbeat.set_status("idle")

        logger.info("Worker %s stopped", self._config.worker_id)

    async def _process_task(self, task_id: str) -> None:
        """Process a single task.

        Phase 3 skeleton -- implements steps 1-2 and 8-9 only:
        1. Read task data from Redis
        2. Mark as running
        3. (future) Download input files from COS -> upload to ComfyUI
        4. (future) Submit workflow to ComfyUI
        5. (future) Wait for completion with progress tracking
        6. (future) Download result from ComfyUI
        7. (future) Upload to COS
        8. (future) Extract last frame -> upload to COS
        9. Mark as completed
        """
        # Step 1: Read task data
        task = await self._gateway.get_task(task_id)
        if task is None:
            logger.warning("Task %s not found in Redis, skipping", task_id)
            return

        if task["status"] != TaskStatus.QUEUED.value:
            logger.warning(
                "Task %s has status %s (expected queued), skipping",
                task_id,
                task["status"],
            )
            return

        # Step 2: Mark as running
        await self._gateway.mark_task_running(task_id)
        logger.info("Task %s marked as running", task_id)

        # Steps 3-7: ComfyUI interaction (to be implemented in later phases)
        # - Download input files from COS
        # - Submit workflow to ComfyUI
        # - Track progress via WebSocket
        # - Download result video
        # - Upload result to COS
        # - Extract last frame

        # Step 9: Mark as completed (skeleton -- no actual video URL)
        await self._gateway.mark_task_completed(task_id)
        logger.info("Task %s completed (skeleton)", task_id)

    async def stop(self) -> None:
        """Signal the worker loop to stop."""
        self._running = False
        logger.info("Worker %s stop requested", self._config.worker_id)
