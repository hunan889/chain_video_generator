"""Global Task Poller — periodic scan for orphan tasks, chains, workflows.

Ported from api/services/task_manager.py _orphan_recovery_loop.
Runs every 30s in the Gateway, recovers stuck tasks and syncs MySQL.
"""

import asyncio
import json
import logging
import time
from typing import Optional

from api_gateway.config import GatewayConfig
from api_gateway.services.task_store import TaskStore
from shared.redis_keys import task_key, chain_key, queue_key
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)

POLL_INTERVAL = 30  # seconds between scans
WORKFLOW_HEARTBEAT_TIMEOUT = 120  # seconds before considering workflow orphaned
MAX_RESUME_RETRIES = 3


class TaskPoller:
    """Periodically scans Redis for orphan tasks/chains/workflows and recovers them.

    Also syncs terminal states to MySQL via TaskStore.
    """

    def __init__(
        self,
        gateway: TaskGateway,
        redis,
        config: GatewayConfig,
        task_store: TaskStore,
    ):
        self.gateway = gateway
        self.redis = redis
        self.config = config
        self.task_store = task_store
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the background poller loop."""
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("TaskPoller started (interval=%ds)", POLL_INTERVAL)

    async def stop(self) -> None:
        """Stop the poller."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("TaskPoller stopped")

    async def _poll_loop(self) -> None:
        """Main loop — scan and recover every POLL_INTERVAL seconds."""
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL)
                await self._recover_orphan_tasks()
                await self._recover_orphan_chains()
                await self._recover_orphan_workflows()
                await self._sync_thirdparty_tasks()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("TaskPoller loop error (will retry)")

    # ------------------------------------------------------------------
    # 1. Orphan Tasks — stuck in running but ComfyUI may have finished
    # ------------------------------------------------------------------
    async def _recover_orphan_tasks(self) -> None:
        """Scan task:* keys in running state, check if actually completed."""
        cursor = 0
        recovered = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="task:*", count=200)
            for key in keys:
                try:
                    status = await self.redis.hget(key, "status")
                    if status != "running":
                        continue

                    task_id = key.split(":", 1)[1]
                    created_at = await self.redis.hget(key, "created_at")
                    age = time.time() - float(created_at or 0)

                    # Only recover tasks older than 10 minutes with no progress
                    if age < 600:
                        continue

                    progress = float(await self.redis.hget(key, "progress") or 0)
                    if progress > 0.05:
                        # Has real progress, likely still running
                        continue

                    # Task stuck: mark as failed
                    await self.redis.hset(key, mapping={
                        "status": "failed",
                        "error": f"Task stuck (running for {int(age)}s with no progress)",
                    })
                    await self.task_store.update_status(
                        task_id, "failed",
                        error=f"Task stuck (running for {int(age)}s with no progress)",
                    )
                    recovered += 1
                    logger.info("Recovered orphan task %s (age=%ds)", task_id, int(age))
                except Exception:
                    pass
            if cursor == 0:
                break
        if recovered:
            logger.info("Recovered %d orphan tasks", recovered)

    # ------------------------------------------------------------------
    # 2. Orphan Chains — sync chain status from segment tasks
    # ------------------------------------------------------------------
    async def _recover_orphan_chains(self) -> None:
        """Scan chain:* keys, sync status from their segment tasks."""
        cursor = 0
        recovered = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="chain:*", count=200)
            for key in keys:
                try:
                    parts = key.split(":")
                    if len(parts) != 2:
                        continue
                    chain_id = parts[1]
                    chain_status = await self.redis.hget(key, "status")
                    if chain_status not in ("running", "queued"):
                        continue

                    # Check segment tasks
                    task_ids_raw = await self.redis.hget(key, "segment_task_ids") or "[]"
                    task_ids = json.loads(task_ids_raw)
                    if not task_ids:
                        continue

                    all_completed = True
                    any_failed = False
                    final_video = ""
                    error_msg = ""

                    for tid in task_ids:
                        t_status = await self.redis.hget(task_key(tid), "status")
                        if t_status == "completed":
                            final_video = await self.redis.hget(task_key(tid), "video_url") or ""
                        elif t_status == "failed":
                            any_failed = True
                            error_msg = await self.redis.hget(task_key(tid), "error") or ""
                        else:
                            all_completed = False

                    if all_completed and not any_failed:
                        await self.redis.hset(key, mapping={
                            "status": "completed",
                            "final_video_url": final_video,
                        })
                        recovered += 1
                        logger.info("Recovered chain %s: all tasks completed", chain_id)
                    elif any_failed:
                        await self.redis.hset(key, mapping={
                            "status": "failed",
                            "error": error_msg,
                        })
                        recovered += 1
                        logger.info("Recovered chain %s: task failed", chain_id)
                except Exception:
                    pass
            if cursor == 0:
                break
        if recovered:
            logger.info("Recovered %d orphan chains", recovered)

    # ------------------------------------------------------------------
    # 3. Orphan Workflows — sync workflow status from chain/stages
    # ------------------------------------------------------------------
    async def _recover_orphan_workflows(self) -> None:
        """Scan workflow:wf_* keys, sync with chain status or detect orphans."""
        cursor = 0
        recovered = 0
        now = int(time.time())

        while True:
            cursor, keys = await self.redis.scan(cursor, match="workflow:wf_*", count=200)
            for key in keys:
                try:
                    if key.count(":") > 1:
                        continue
                    status = await self.redis.hget(key, "status")
                    if status != "running":
                        continue

                    workflow_id = key.split(":", 1)[1]
                    chain_id = await self.redis.hget(key, "chain_id")

                    if chain_id:
                        # Has a chain — sync from chain status
                        chain_status = await self.redis.hget(chain_key(chain_id), "status")
                        if chain_status == "completed":
                            chain_video = await self.redis.hget(chain_key(chain_id), "final_video_url") or ""
                            await self.redis.hset(key, mapping={
                                "status": "completed",
                                "final_video_url": chain_video,
                                "completed_at": str(now),
                            })
                            await self.task_store.update_status(workflow_id, "completed")
                            await self.task_store.set_result(workflow_id, result_url=chain_video)
                            recovered += 1
                            logger.info("Recovered workflow %s: chain completed", workflow_id)
                        elif chain_status in ("failed", "partial"):
                            chain_error = await self.redis.hget(chain_key(chain_id), "error") or "Chain failed"
                            await self.redis.hset(key, mapping={
                                "status": "failed",
                                "error": chain_error,
                                "completed_at": str(now),
                            })
                            await self.task_store.update_status(workflow_id, "failed", error=chain_error)
                            recovered += 1
                            logger.info("Recovered workflow %s: chain %s", workflow_id, chain_status)
                    else:
                        # No chain — check heartbeat for orphan detection
                        heartbeat = await self.redis.hget(key, "executor_heartbeat")
                        created_at = await self.redis.hget(key, "created_at")
                        last_active = int(heartbeat or created_at or 0)
                        if now - last_active < WORKFLOW_HEARTBEAT_TIMEOUT:
                            continue  # Still active

                        # Orphaned — mark as failed
                        await self.redis.hset(key, mapping={
                            "status": "failed",
                            "error": f"Workflow orphaned (no heartbeat for {now - last_active}s)",
                            "completed_at": str(now),
                        })
                        await self.task_store.update_status(
                            workflow_id, "failed",
                            error=f"Workflow orphaned (no heartbeat for {now - last_active}s)",
                        )
                        recovered += 1
                        logger.info("Recovered orphan workflow %s (heartbeat age=%ds)", workflow_id, now - last_active)
                except Exception:
                    pass
            if cursor == 0:
                break
        if recovered:
            logger.info("Recovered %d orphan workflows", recovered)

    # ------------------------------------------------------------------
    # 4. Third-party task sync — poll Wan2.6/Seedance for pending tasks
    # ------------------------------------------------------------------
    async def _sync_thirdparty_tasks(self) -> None:
        """Query MySQL for queued/running thirdparty tasks and poll their APIs."""
        try:
            result = await self.task_store.list_history(
                category="thirdparty", status="queued", page=1, page_size=50,
            )
            tasks = result.get("tasks", [])
            if not tasks:
                return

            import aiohttp
            for task in tasks:
                try:
                    task_id = task.get("task_id", "")
                    provider = task.get("provider", "")
                    external_id = task.get("external_task_id") or task_id

                    if provider == "wan26":
                        await self._poll_wan26(external_id, task_id)
                    elif provider == "seedance":
                        await self._poll_seedance(external_id, task_id)
                except Exception:
                    logger.debug("Failed to poll thirdparty task %s", task.get("task_id"), exc_info=True)
        except Exception:
            logger.debug("Thirdparty sync error", exc_info=True)

    async def _poll_wan26(self, external_id: str, task_id: str) -> None:
        """Poll Wan2.6 DashScope API for task status."""
        import aiohttp
        url = f"{self.config.wan26_api_url}/{external_id}"
        headers = {"Authorization": f"Bearer {self.config.wan26_api_key}"}
        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    # API error — task may not exist anymore
                    body_text = await resp.text()
                    logger.info("Wan2.6 task %s query returned %d: %s", task_id, resp.status, body_text[:100])
                    if resp.status in (400, 404):
                        await self.task_store.update_status(
                            task_id, "failed", error=f"Wan2.6 API: {resp.status} (task not found)",
                        )
                    return
                body = await resp.json()
                output = body.get("output", {})
                status = output.get("task_status", "")

                if status == "SUCCEEDED":
                    video_url = output.get("video_url")
                    await self.task_store.update_status(task_id, "completed")
                    if video_url:
                        await self.task_store.set_result(task_id, result_url=video_url)
                    logger.info("Wan2.6 task %s completed: %s", task_id, video_url)
                elif status == "FAILED":
                    error = output.get("message", "Task failed")
                    await self.task_store.update_status(task_id, "failed", error=error)
                    logger.info("Wan2.6 task %s failed: %s", task_id, error)

    async def _poll_seedance(self, external_id: str, task_id: str) -> None:
        """Poll Seedance BytePlus API for task status."""
        import aiohttp
        url = f"{self.config.byteplus_api_url}/contents/generations/tasks/{external_id}"
        headers = {"Authorization": f"Bearer {self.config.byteplus_api_key}"}
        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return
                body = await resp.json()
                raw_status = body.get("status", "").lower()

                if raw_status == "succeeded":
                    video_url = body.get("content", {}).get("video_url")
                    await self.task_store.update_status(task_id, "completed")
                    if video_url:
                        await self.task_store.set_result(task_id, result_url=video_url)
                    logger.info("Seedance task %s completed: %s", task_id, video_url)
                elif raw_status == "failed":
                    err = body.get("error", {})
                    error = f"{err.get('code', 'Error')}: {err.get('message', 'Unknown')}"
                    await self.task_store.update_status(task_id, "failed", error=error)
                    logger.info("Seedance task %s failed: %s", task_id, error)
