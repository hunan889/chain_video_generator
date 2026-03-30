import asyncio
import json
import logging
import time
import uuid
from typing import Optional
from urllib.parse import urlparse
import redis.asyncio as aioredis
from api.config import REDIS_URL, COMFYUI_URLS, VIDEO_BASE_URL, TASK_EXPIRY, COS_ENABLED
from api.models.enums import TaskStatus, ModelType, GenerateMode
from api.models.schemas import GenerateRequest, GenerateI2VRequest
from api.services.comfyui_client import ComfyUIClient
from api.services.workflow_builder import build_workflow, build_story_workflow, build_merged_story_workflow, _inject_story_postproc, _inject_lossless_frame_save
from api.services import storage

logger = logging.getLogger(__name__)

MAX_OOM_RETRIES = 2  # Max retry attempts for CUDA OOM errors
OOM_COOLDOWN = 5     # Seconds to wait after OOM before retrying

REDIS_INSTANCES_PREFIX = "comfyui_instances"


def _is_oom_error(error: Exception) -> bool:
    """Check if error is a CUDA OOM error."""
    msg = str(error).lower()
    return "cuda out of memory" in msg or "out of memory" in msg


class _HistoryReady(Exception):
    """Internal signal: ComfyUI history has outputs ready."""
    def __init__(self, history: dict):
        self.history = history


def _worker_id_from_url(model_key: str, url: str) -> str:
    """Derive worker_id from model_key and URL, e.g. 'a14b:8188'."""
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{model_key}:{port}"


class TaskManager:
    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self._worker_redis: Optional[aioredis.Redis] = None
        self.clients: dict[str, list[ComfyUIClient]] = {}
        self._workers: dict[str, dict] = {}  # worker_id -> {model_key, url, client, task, started_at}
        self._chain_workers: dict[str, asyncio.Task] = {}  # chain_id -> asyncio.Task
        self._direct_busy_urls: set[str] = set()  # URLs busy with direct prompts (face swap etc.)

    async def start(self):
        self.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        self._worker_redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        # Load worker URLs: Redis (persisted) merged with .env (seed)
        for model_key in COMFYUI_URLS:
            self.clients.setdefault(model_key, [])
        for model_key, env_urls in COMFYUI_URLS.items():
            redis_key = f"{REDIS_INSTANCES_PREFIX}:{model_key}"
            redis_urls = await self.redis.smembers(redis_key)
            # Merge: Redis takes priority, .env URLs are seeds added if Redis is empty
            if redis_urls:
                urls = list(redis_urls)
                # Also add any .env URLs not yet in Redis (new seeds)
                for u in env_urls:
                    if u not in redis_urls:
                        urls.append(u)
                        await self.redis.sadd(redis_key, u)
            else:
                urls = list(env_urls)
                # Seed Redis with .env URLs
                if urls:
                    await self.redis.sadd(redis_key, *urls)
            for url in urls:
                try:
                    await self.add_worker(model_key, url)
                except Exception as e:
                    logger.warning("Failed to add worker %s %s at startup: %s", model_key, url, e)
        # Recover orphan running tasks (from previous crash/restart)
        await self._recover_orphan_tasks()
        await self._recover_orphan_chains()
        await self._recover_orphan_workflows()
        # Start periodic orphan recovery loop
        self._orphan_recovery_task = asyncio.create_task(self._orphan_recovery_loop())
        logger.info("TaskManager started with workers: %s",
                     {k: len(v) for k, v in self.clients.items()})

    async def stop(self):
        # Cancel orphan recovery loop
        if hasattr(self, '_orphan_recovery_task') and self._orphan_recovery_task:
            self._orphan_recovery_task.cancel()
        # Cancel chain workers first (C2)
        for chain_task in self._chain_workers.values():
            chain_task.cancel()
        self._chain_workers.clear()
        for info in self._workers.values():
            info["task"].cancel()
        self._workers.clear()
        for client_list in self.clients.values():
            for client in client_list:
                await client.close()
        if self.redis:
            await self.redis.close()
        if self._worker_redis:
            await self._worker_redis.close()

    # ── Dynamic worker management ────────────────────────────────────

    async def add_worker(self, model_key: str, url: str) -> str:
        """Add a new ComfyUI worker at runtime. Returns worker_id."""
        url = url.rstrip("/")
        worker_id = _worker_id_from_url(model_key, url)
        if worker_id in self._workers:
            raise ValueError(f"Worker {worker_id} already exists")
        # Create client and verify it's reachable
        client = ComfyUIClient(url)
        alive = await client.is_alive()
        if not alive:
            await client.close()
            raise RuntimeError(f"ComfyUI instance at {url} is not reachable")
        # Register client
        self.clients.setdefault(model_key, [])
        self.clients[model_key].append(client)
        # Start worker loop
        task = asyncio.create_task(self._worker_loop(model_key, client, worker_id))
        self._workers[worker_id] = {
            "model_key": model_key,
            "url": url,
            "client": client,
            "task": task,
            "started_at": time.time(),
        }
        # Persist to Redis
        redis_key = f"{REDIS_INSTANCES_PREFIX}:{model_key}"
        await self.redis.sadd(redis_key, url)
        logger.info("Worker %s added (%s)", worker_id, url)
        return worker_id

    async def remove_worker(self, worker_id: str, force: bool = False) -> bool:
        """Remove a worker. Cancels its loop task and cleans up."""
        info = self._workers.pop(worker_id, None)
        if not info:
            return False
        # Cancel the worker loop
        info["task"].cancel()
        # Remove client from clients list
        model_key = info["model_key"]
        client = info["client"]
        if model_key in self.clients:
            try:
                self.clients[model_key].remove(client)
            except ValueError:
                pass
        await client.close()
        # Remove from Redis
        redis_key = f"{REDIS_INSTANCES_PREFIX}:{model_key}"
        await self.redis.srem(redis_key, info["url"])
        logger.info("Worker %s removed", worker_id)
        return True

    async def list_workers(self) -> list[dict]:
        """List all active workers with status."""
        results = []
        for wid, info in self._workers.items():
            alive = await info["client"].is_alive()
            results.append({
                "id": wid,
                "model_key": info["model_key"],
                "url": info["url"],
                "alive": alive,
                "started_at": info["started_at"],
            })
        return results

    async def get_running_tasks_by_worker(self) -> dict[str, dict]:
        """Return a mapping of worker URL -> running task info.

        Scans Redis for tasks with status=running and groups them by comfyui_url.
        Only running tasks are returned, so the scan is typically very small.
        """
        result: dict[str, dict] = {}
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="task:*", count=200)
            for key in keys:
                status, comfyui_url = await self.redis.hmget(key, "status", "comfyui_url")
                if status != TaskStatus.RUNNING.value or not comfyui_url:
                    continue
                task_id = key.split(":", 1)[1] if ":" in key else key
                # Fetch task summary
                progress, mode, model, params_raw, created_at = await self.redis.hmget(
                    key, "progress", "mode", "model", "params", "created_at"
                )
                params = None
                if params_raw:
                    try:
                        params = json.loads(params_raw)
                    except Exception:
                        pass
                result[comfyui_url] = {
                    "task_id": task_id,
                    "progress": float(progress) if progress else 0,
                    "mode": mode or None,
                    "model": model or None,
                    "prompt": (params.get("prompt") or params.get("positive_prompt") or "")[:100] if params else "",
                    "created_at": int(created_at) if created_at else None,
                }
            if cursor == 0:
                break
        return result

    async def find_available_client(self, prefer_model_key: str = None, timeout: float = 120) -> "ComfyUIClient | None":
        """Find an idle ComfyUI worker, waiting if all are busy.

        Considers both Redis-tracked tasks (video generation) and in-memory
        direct-prompt tracking (face swap) to avoid piling onto one worker.

        Args:
            prefer_model_key: If set, prefer idle workers with this model_key.
            timeout: Max seconds to wait for an idle worker.

        Returns:
            An idle ComfyUIClient, or None if timeout expires.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            busy_urls = set((await self.get_running_tasks_by_worker()).keys())
            busy_urls |= self._direct_busy_urls  # include face-swap etc.
            preferred = []
            fallback = []
            for wid, info in self._workers.items():
                if info["url"] not in busy_urls:
                    if prefer_model_key and info["model_key"] == prefer_model_key:
                        preferred.append(info["client"])
                    else:
                        fallback.append(info["client"])
            idle = preferred or fallback
            if idle:
                # Pick randomly to spread load across idle workers
                import random
                client = random.choice(idle)
                self._direct_busy_urls.add(client.base_url)
                return client
            logger.debug("find_available_client: all workers busy, retrying in 2s…")
            await asyncio.sleep(2)
        logger.warning("find_available_client: timeout after %.0fs, no idle worker found", timeout)
        return None

    def release_client(self, client: "ComfyUIClient"):
        """Mark a directly-used client as idle again (call after face swap etc.)."""
        self._direct_busy_urls.discard(client.base_url)

    def _get_client(self, model_key: str) -> "ComfyUIClient | None":
        """Get first ComfyUI client for a model key (shared filesystem — any instance works)."""
        clients = self.clients.get(model_key)
        return clients[0] if clients else None

    def _get_client_by_url(self, model_key: str, url: str) -> "ComfyUIClient | None":
        """Get the specific ComfyUI client matching a URL."""
        for client in self.clients.get(model_key, []):
            if client.base_url == url:
                return client
        return self._get_client(model_key)

    async def redis_alive(self) -> bool:
        try:
            return await self.redis.ping()
        except Exception:
            return False

    async def create_task(self, mode: GenerateMode, model: ModelType, workflow: dict, params: Optional[dict] = None, chain_id: Optional[str] = None) -> str:
        task_id = uuid.uuid4().hex
        import time
        task_data = {
            "status": TaskStatus.QUEUED.value,
            "mode": mode.value,
            "model": model.value,
            "workflow": json.dumps(workflow),
            "progress": "0",
            "video_url": "",
            "error": "",
            "created_at": str(int(time.time())),
        }
        if params:
            task_data["params"] = json.dumps(params)
        if chain_id:
            task_data["chain_id"] = chain_id
        await self.redis.hset(f"task:{task_id}", mapping=task_data)
        await self.redis.expire(f"task:{task_id}", TASK_EXPIRY)
        await self.redis.rpush(f"queue:{model.value}", task_id)
        logger.info("Task %s created for %s/%s%s", task_id, mode.value, model.value, f" (chain {chain_id})" if chain_id else "")
        return task_id

    async def get_task(self, task_id: str) -> Optional[dict]:
        data = await self.redis.hgetall(f"task:{task_id}")
        if not data:
            return None
        params = None
        if data.get("params"):
            try:
                params = json.loads(data["params"])
            except Exception:
                pass

        # Debug: log what we got from Redis
        logger.info(f"Task {task_id} last_frame_url from Redis: {data.get('last_frame_url')}")

        return {
            "task_id": task_id,
            "status": data.get("status", "unknown"),
            "mode": data.get("mode") or None,
            "model": data.get("model") or None,
            "progress": float(data.get("progress", 0)),
            "video_url": data.get("video_url") or None,
            "last_frame_url": data.get("last_frame_url") or None,
            "error": data.get("error") or None,
            "retry_count": int(data.get("retry_count", 0)),
            "params": params,
            "created_at": int(data["created_at"]) if data.get("created_at") else None,
            "completed_at": int(data["completed_at"]) if data.get("completed_at") else None,
        }

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running or queued task."""
        data = await self.redis.hgetall(f"task:{task_id}")
        if not data:
            return False
        status = data.get("status")
        if status == TaskStatus.QUEUED.value:
            # Remove from queue
            model = data.get("model", "")
            await self.redis.lrem(f"queue:{model}", 0, task_id)
            await self.redis.hset(f"task:{task_id}", mapping={
                "status": TaskStatus.FAILED.value,
                "error": "Cancelled by user",
                "completed_at": str(int(__import__('time').time())),
            })
            return True
        elif status == TaskStatus.RUNNING.value:
            prompt_id = data.get("prompt_id")
            model = data.get("model", "")
            comfyui_url = data.get("comfyui_url", "")
            client = self._get_client_by_url(model, comfyui_url) if comfyui_url else self._get_client(model)
            if client and prompt_id:
                await client.interrupt()
                await client.cancel_prompt(prompt_id)
            await self.redis.hset(f"task:{task_id}", mapping={
                "status": TaskStatus.FAILED.value,
                "error": "Cancelled by user",
                "completed_at": str(int(__import__('time').time())),
            })
            return True
        return False

    async def list_tasks(self) -> list[dict]:
        """List all tasks from Redis, excluding chain segment tasks."""
        tasks = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="task:*", count=100)
            for key in keys:
                task_id = key.split(":", 1)[1]
                data = await self.redis.hgetall(key)
                if data:
                    # Skip tasks that belong to a chain
                    if data.get("chain_id"):
                        continue
                    params = None
                    if data.get("params"):
                        try:
                            params = json.loads(data["params"])
                        except Exception:
                            pass
                    created_at = int(data["created_at"]) if data.get("created_at") else 0
                    completed_at = int(data["completed_at"]) if data.get("completed_at") else None
                    tasks.append({
                        "task_id": task_id,
                        "status": data.get("status", "unknown"),
                        "mode": data.get("mode", ""),
                        "model": data.get("model", ""),
                        "progress": float(data.get("progress", 0)),
                        "video_url": data.get("video_url") or None,
                        "last_frame_url": data.get("last_frame_url") or None,
                        "error": data.get("error") or None,
                        "params": params,
                        "created_at": created_at or None,
                        "completed_at": completed_at,
                    })
            if cursor == 0:
                break
        # Sort: running/queued first, then by created_at descending
        order = {"running": 0, "queued": 1, "completed": 2, "failed": 3}
        tasks.sort(key=lambda t: (order.get(t["status"], 9), -(t.get("created_at") or 0)))
        return tasks

    async def list_chains(self) -> list[dict]:
        """List all chains from Redis."""
        chains = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="chain:*", count=100)
            for key in keys:
                parts = key.split(":")
                if len(parts) != 2:
                    continue
                chain_id = parts[1]
                chain = await self.get_chain(chain_id)
                if chain:
                    chains.append(chain)
            if cursor == 0:
                break
        order = {"running": 0, "queued": 1, "completed": 2, "partial": 3, "failed": 4}
        chains.sort(key=lambda c: (order.get(c["status"], 9), -(c.get("created_at") or 0)))
        return chains

    async def _recover_orphan_tasks(self):
        """Recover tasks stuck in 'running' state by checking ComfyUI for their prompt_id."""
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="task:*", count=100)
            for key in keys:
                status = await self.redis.hget(key, "status")
                if status == TaskStatus.RUNNING.value:
                    task_id = key.split(":", 1)[1]
                    prompt_id = await self.redis.hget(key, "prompt_id")
                    model_key = await self.redis.hget(key, "model")
                    comfyui_url = await self.redis.hget(key, "comfyui_url")
                    if model_key and comfyui_url:
                        client = self._get_client_by_url(model_key, comfyui_url)
                    elif model_key:
                        client = self._get_client(model_key)
                    else:
                        client = None

                    if not prompt_id or not client:
                        await self.redis.hset(key, mapping={
                            "status": TaskStatus.FAILED.value,
                            "error": "Service restarted, no prompt_id to recover.",
                        })
                        logger.info("Orphan task %s has no prompt_id, marked failed", task_id)
                        continue

                    # Check if ComfyUI already finished this prompt
                    try:
                        history = await client.get_history(prompt_id)
                        if history and history.get("outputs"):
                            # Already done — collect results in background
                            logger.info("Recovering completed task %s (prompt %s)", task_id, prompt_id)
                            asyncio.create_task(self._collect_result(task_id, prompt_id, client))
                        else:
                            # Still running — resume monitoring in background
                            logger.info("Resuming monitoring for task %s (prompt %s)", task_id, prompt_id)
                            asyncio.create_task(self._resume_task(task_id, prompt_id, client))
                    except Exception as e:
                        logger.warning("Failed to check ComfyUI for task %s: %s", task_id, e)
                        await self.redis.hset(key, mapping={
                            "status": TaskStatus.FAILED.value,
                            "error": f"Recovery failed: {e}",
                        })
            if cursor == 0:
                break

    async def _recover_orphan_chains(self):
        """Recover chains stuck in running/queued after API restart (C1)."""
        cursor = 0
        recovered = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="chain:*", count=100)
            for key in keys:
                parts = key.split(":")
                if len(parts) != 2:
                    continue
                chain_id = parts[1]
                status = await self.redis.hget(key, "status")
                if status not in ("running", "queued"):
                    continue

                task_ids_raw = await self.redis.hget(key, "segment_task_ids") or "[]"
                try:
                    task_ids = json.loads(task_ids_raw)
                except Exception:
                    task_ids = []

                if not task_ids:
                    # No tasks ever created — mark failed
                    await self.redis.hset(key, mapping={
                        "status": "failed",
                        "error": "Service restarted before any segment task was created",
                        "completed_at": str(int(time.time())),
                    })
                    logger.info("Orphan chain %s: no tasks, marked failed", chain_id)
                    recovered += 1
                    continue

                # Check all segment task statuses
                all_completed = True
                any_failed = False
                any_running = False
                fail_error = ""
                for tid in task_ids:
                    t_status = await self.redis.hget(f"task:{tid}", "status")
                    if t_status == TaskStatus.RUNNING.value or t_status == TaskStatus.QUEUED.value:
                        any_running = True
                        all_completed = False
                    elif t_status == TaskStatus.FAILED.value:
                        any_failed = True
                        all_completed = False
                        fail_error = await self.redis.hget(f"task:{tid}", "error") or "Unknown"
                    elif t_status != TaskStatus.COMPLETED.value:
                        all_completed = False

                if any_running:
                    # Tasks still being recovered by _recover_orphan_tasks, leave chain alone
                    logger.info("Orphan chain %s: has running tasks, deferring recovery", chain_id)
                    continue

                if all_completed:
                    # All tasks done but chain wasn't finalized — get last task's video
                    last_task_data = await self.redis.hgetall(f"task:{task_ids[-1]}")
                    video_url = last_task_data.get("video_url", "")
                    completed_count = len(task_ids)
                    await self.redis.hset(key, mapping={
                        "status": "completed",
                        "completed_segments": str(completed_count),
                        "final_video_url": video_url,
                        "completed_at": str(int(time.time())),
                    })
                    logger.info("Orphan chain %s: all %d tasks completed, recovered with video %s",
                                chain_id, completed_count, video_url)
                elif any_failed:
                    completed_count = 0
                    for tid in task_ids:
                        t_st = await self.redis.hget(f"task:{tid}", "status")
                        if t_st == TaskStatus.COMPLETED.value:
                            completed_count += 1
                    chain_status = "partial" if completed_count > 0 else "failed"
                    await self.redis.hset(key, mapping={
                        "status": chain_status,
                        "completed_segments": str(completed_count),
                        "error": f"Service restarted; segment failed: {fail_error}",
                        "completed_at": str(int(time.time())),
                    })
                    logger.info("Orphan chain %s: marked %s (%d/%d completed)", chain_id,
                                chain_status, completed_count, len(task_ids))
                else:
                    # Unknown task states — mark failed
                    await self.redis.hset(key, mapping={
                        "status": "failed",
                        "error": "Service restarted, chain state unrecoverable",
                        "completed_at": str(int(time.time())),
                    })
                    logger.info("Orphan chain %s: unknown task states, marked failed", chain_id)
                recovered += 1
            if cursor == 0:
                break
        if recovered:
            logger.info("Recovered %d orphan chains", recovered)

    async def _recover_orphan_workflows(self):
        """Recover workflows stuck in running after API restart (C1)."""
        cursor = 0
        recovered = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="workflow:wf_*", count=100)
            for key in keys:
                # Skip sub-keys like workflow:wf_xxx:req
                if key.count(":") > 1:
                    continue
                status = await self.redis.hget(key, "status")
                if status != "running":
                    continue

                workflow_id = key.split(":", 1)[1]
                chain_id = await self.redis.hget(key, "chain_id")

                if chain_id:
                    # Has a chain — sync workflow status with chain
                    chain_status = await self.redis.hget(f"chain:{chain_id}", "status")
                    if chain_status == "completed":
                        chain_video = await self.redis.hget(f"chain:{chain_id}", "final_video_url") or ""
                        await self.redis.hset(key, mapping={
                            "status": "completed",
                            "final_video_url": chain_video,
                            "completed_at": str(int(time.time())),
                        })
                        logger.info("Orphan workflow %s: chain completed, recovered", workflow_id)
                    elif chain_status in ("failed", "partial"):
                        chain_error = await self.redis.hget(f"chain:{chain_id}", "error") or "Chain failed"
                        await self.redis.hset(key, mapping={
                            "status": "failed",
                            "error": f"Service restarted; {chain_error}",
                            "completed_at": str(int(time.time())),
                        })
                        logger.info("Orphan workflow %s: chain %s, marked failed", workflow_id, chain_status)
                    elif chain_status in ("running", "queued"):
                        # Chain still being processed (task recovery will handle it)
                        logger.info("Orphan workflow %s: chain still %s, deferring", workflow_id, chain_status)
                        continue
                    else:
                        await self.redis.hset(key, mapping={
                            "status": "failed",
                            "error": "Service restarted, chain state unknown",
                            "completed_at": str(int(time.time())),
                        })
                        logger.info("Orphan workflow %s: chain status=%s, marked failed", workflow_id, chain_status)
                else:
                    # No chain_id — might be stuck in Stage 1-3, or still actively processing.
                    # Check executor heartbeat to avoid resuming workflows that are still running.
                    heartbeat_str = await self.redis.hget(key, "executor_heartbeat")
                    created_at_str = await self.redis.hget(key, "created_at")
                    now = int(time.time())
                    last_active = int(heartbeat_str) if heartbeat_str else (int(created_at_str) if created_at_str else 0)
                    if now - last_active < 120:
                        # Executor was active within the last 120 seconds — not an orphan
                        # (face swap via Reactor can take 60-90s)
                        logger.info("Orphan workflow %s: executor active %ds ago, skipping", workflow_id, now - last_active)
                        continue

                    retry_count = int(await self.redis.hget(key, "resume_count") or 0)
                    if retry_count >= 3:
                        await self.redis.hset(key, mapping={
                            "status": "failed",
                            "error": "Service restarted during pre-video stages (max retries reached)",
                            "completed_at": str(int(time.time())),
                        })
                        logger.info("Orphan workflow %s: max resume retries, marked failed", workflow_id)
                    else:
                        await self.redis.hset(key, "resume_count", str(retry_count + 1))
                        try:
                            await self._resume_workflow(workflow_id)
                            logger.info("Orphan workflow %s: resuming (attempt %d)", workflow_id, retry_count + 1)
                        except Exception as e:
                            logger.error("Orphan workflow %s: resume failed: %s", workflow_id, e)
                            await self.redis.hset(key, mapping={
                                "status": "failed",
                                "error": f"Resume failed: {e}",
                                "completed_at": str(int(time.time())),
                            })
                recovered += 1
            if cursor == 0:
                break
        if recovered:
            logger.info("Recovered %d orphan workflows", recovered)

    async def _orphan_recovery_loop(self):
        """Periodically scan for orphan tasks/chains/workflows and recover them.

        This catches cases where WebSocket completion detection fails
        (connection drops, network issues, etc.) so tasks don't get stuck forever.
        """
        INTERVAL = 30  # seconds between scans
        logger.info("Orphan recovery loop started (interval=%ds)", INTERVAL)
        try:
            while True:
                await asyncio.sleep(INTERVAL)
                try:
                    await self._recover_orphan_tasks()
                    await self._recover_orphan_chains()
                    await self._recover_orphan_workflows()
                except Exception as e:
                    logger.warning("Orphan recovery loop error: %s", e)
        except asyncio.CancelledError:
            logger.info("Orphan recovery loop stopped")

    async def _resume_workflow(self, workflow_id: str):
        """Resume an orphaned workflow from its last completed stage."""
        import asyncio
        import json as _json
        from api.routes.workflow_executor import _execute_workflow

        workflow_data = await self.redis.hgetall(f"workflow:{workflow_id}")
        if not workflow_data:
            raise Exception(f"Workflow {workflow_id} not found")

        # Reconstruct the request from Redis
        from api.routes.workflow import WorkflowGenerateRequest
        req_data = {
            "mode": workflow_data.get("mode"),
            "user_prompt": workflow_data.get("user_prompt"),
        }
        ref_img = workflow_data.get("reference_image")
        if ref_img:
            req_data["reference_image"] = ref_img
        ic_raw = workflow_data.get("internal_config")
        if ic_raw:
            try:
                req_data["internal_config"] = _json.loads(ic_raw)
            except (ValueError, TypeError):
                pass
        parent_wf = workflow_data.get("parent_workflow_id")
        if parent_wf:
            req_data["parent_workflow_id"] = parent_wf

        req = WorkflowGenerateRequest(**req_data)

        # Re-launch the executor with resume=True
        from api.routes.workflow_executor import _active_workflow_tasks
        task = asyncio.create_task(_execute_workflow(workflow_id, req, self, resume=True))
        _active_workflow_tasks.add(task)
        task.add_done_callback(lambda t: _active_workflow_tasks.discard(t))
        def _done_cb(t, wid=workflow_id):
            if t.exception():
                import time as _time
                logger.error("Resumed workflow %s failed: %s", wid, t.exception())
                asyncio.get_event_loop().create_task(self.redis.hset(f"workflow:{wid}", mapping={
                    "status": "failed",
                    "error": f"Resume failed: {t.exception()}",
                    "completed_at": str(int(_time.time())),
                }))
        task.add_done_callback(_done_cb)

    async def _collect_result(self, task_id: str, prompt_id: str, client: ComfyUIClient):
        """Collect output from an already-completed ComfyUI prompt."""
        try:
            await self.redis.hset(f"task:{task_id}", "progress", "0.9")
            output_files = await client.get_output_files(prompt_id)
            if not output_files:
                raise RuntimeError("No output files in completed prompt")
            f = output_files[0]
            data = await client.download_file(
                f["filename"], f.get("subfolder", ""), f.get("type", "output")
            )
            ext = f["filename"].rsplit(".", 1)[-1] if "." in f["filename"] else "mp4"
            result = await storage.save_video(data, ext)
            video_url = result if COS_ENABLED else f"{VIDEO_BASE_URL}/{result}"

            # Extract last frame for video files
            last_frame_url = None
            if ext in ("mp4", "webm", "avi", "mov"):
                try:
                    video_path = await storage.get_video_path_from_url(video_url)
                    if video_path and video_path.exists():
                        from api.services.ffmpeg_utils import extract_last_frame
                        frame_path = await extract_last_frame(video_path)
                        # Save frame and get URL (use local proxy to avoid CORS)
                        frame_data = frame_path.read_bytes()
                        frame_filename, _ = await storage.save_upload(frame_data, frame_path.name)
                        last_frame_url = f"{VIDEO_BASE_URL}/{frame_filename}"
                        logger.info("Extracted last frame for recovered task %s: %s", task_id, last_frame_url)
                except Exception as e:
                    logger.warning("Failed to extract last frame for recovered task %s: %s", task_id, e)

            task_data = {
                "status": TaskStatus.COMPLETED.value,
                "progress": "1.0",
                "video_url": video_url,
                "completed_at": str(int(__import__('time').time())),
            }
            if last_frame_url:
                task_data["last_frame_url"] = last_frame_url

            await self.redis.hset(f"task:{task_id}", mapping=task_data)
            logger.info("Recovered task %s completed: %s", task_id, video_url)
        except Exception as e:
            logger.exception("Recovery collect failed for task %s: %s", task_id, e)
            await self.redis.hset(f"task:{task_id}", mapping={
                "status": TaskStatus.FAILED.value,
                "error": f"Recovery failed: {e}",
            })

    async def _resume_task(self, task_id: str, prompt_id: str, client: ComfyUIClient):
        """Resume monitoring a still-running ComfyUI prompt after API restart."""
        try:
            history = await self._wait_with_progress(client, prompt_id, task_id, timeout=1800)
            await self._collect_result(task_id, prompt_id, client)
        except Exception as e:
            logger.exception("Resume monitoring failed for task %s: %s", task_id, e)
            await self.redis.hset(f"task:{task_id}", mapping={
                "status": TaskStatus.FAILED.value,
                "error": f"Resume failed: {e}",
            })

    async def _worker_loop(self, model_key: str, client: ComfyUIClient, worker_name: str):
        queue_name = f"queue:{model_key}"
        # Each worker needs its own Redis connection for blocking blpop
        worker_redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        logger.info("Worker %s started (queue=%s, url=%s)", worker_name, queue_name, client.base_url)
        try:
            while True:
                try:
                    result = await worker_redis.blpop(queue_name, timeout=5)
                    if result is None:
                        continue
                    _, task_id = result
                    await self._process_task(task_id, client, worker_name)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.exception("Worker %s error: %s", worker_name, e)
                    await asyncio.sleep(2)
        finally:
            await worker_redis.close()

    async def _process_task(self, task_id: str, client: ComfyUIClient, worker_name: str = ""):
        try:
            t_start = time.time()
            await self.redis.hset(f"task:{task_id}", mapping={
                "status": TaskStatus.RUNNING.value,
                "comfyui_url": client.base_url,
            })
            workflow_json = await self.redis.hget(f"task:{task_id}", "workflow")
            if not workflow_json:
                logger.warning("Task %s has no workflow data, skipping", task_id)
                await self.redis.hset(f"task:{task_id}", mapping={
                    "status": TaskStatus.FAILED.value,
                    "error": "No workflow data found",
                    "completed_at": str(int(time.time())),
                })
                return
            workflow = json.loads(workflow_json)

            # Submit to ComfyUI
            t_submit = time.time()
            prompt_id = await client.queue_prompt(workflow)
            await self.redis.hset(f"task:{task_id}", mapping={
                "progress": "0.05",
                "prompt_id": prompt_id,
            })
            logger.info("Task %s [%s]: submit took %.2fs", task_id, worker_name or client.base_url, time.time() - t_submit)

            # Wait for completion with progress updates
            t_wait = time.time()
            history = await self._wait_with_progress(client, prompt_id, task_id, timeout=1800)
            await self.redis.hset(f"task:{task_id}", "progress", "0.9")
            logger.info("Task %s: generation took %.2fs", task_id, time.time() - t_wait)

            # Download output files
            t_download = time.time()
            output_files = await client.get_output_files(prompt_id)
            if not output_files:
                raise RuntimeError("No output files generated")

            f = output_files[0]
            data = await client.download_file(
                f["filename"], f.get("subfolder", ""), f.get("type", "output")
            )
            ext = f["filename"].rsplit(".", 1)[-1] if "." in f["filename"] else "mp4"
            result = await storage.save_video(data, ext)
            video_url = result if COS_ENABLED else f"{VIDEO_BASE_URL}/{result}"
            logger.info("Task %s: download+save took %.2fs", task_id, time.time() - t_download)

            # Extract last frame for video files
            last_frame_url = None
            if ext in ("mp4", "webm", "avi", "mov"):
                try:
                    t_frame = time.time()
                    video_path = await storage.get_video_path_from_url(video_url)
                    if video_path and video_path.exists():
                        from api.services.ffmpeg_utils import extract_last_frame
                        frame_path = await extract_last_frame(video_path)
                        # Save frame and get URL (use local proxy to avoid CORS)
                        frame_data = frame_path.read_bytes()
                        frame_filename, _ = await storage.save_upload(frame_data, frame_path.name)
                        last_frame_url = f"{VIDEO_BASE_URL}/{frame_filename}"
                        logger.info("Task %s: frame extraction took %.2fs, url: %s", task_id, time.time() - t_frame, last_frame_url)
                except Exception as e:
                    logger.warning("Failed to extract last frame for task %s: %s", task_id, e)

            task_data = {
                "status": TaskStatus.COMPLETED.value,
                "progress": "1.0",
                "video_url": video_url,
                "completed_at": str(int(__import__('time').time())),
            }
            if last_frame_url:
                task_data["last_frame_url"] = last_frame_url

            await self.redis.hset(f"task:{task_id}", mapping=task_data)
            logger.info("Task %s completed in %.2fs total: %s", task_id, time.time() - t_start, video_url)

        except Exception as e:
            error_msg = str(e)
            is_oom = _is_oom_error(e)

            if is_oom:
                retry_count = int(await self.redis.hget(f"task:{task_id}", "retry_count") or 0)
                logger.warning(
                    "Task %s OOM (retry %d/%d): %s",
                    task_id, retry_count, MAX_OOM_RETRIES, error_msg[:200],
                )

                # Call /free to release GPU memory
                try:
                    await client.free_memory()
                    logger.info("Task %s: free_memory called on %s", task_id, client.base_url)
                except Exception as free_err:
                    logger.warning("Task %s: free_memory failed: %s", task_id, free_err)

                if retry_count < MAX_OOM_RETRIES:
                    await asyncio.sleep(OOM_COOLDOWN)

                    # Re-queue the task
                    model = await self.redis.hget(f"task:{task_id}", "model")
                    await self.redis.hset(f"task:{task_id}", mapping={
                        "status": TaskStatus.QUEUED.value,
                        "retry_count": str(retry_count + 1),
                        "error": f"OOM retry {retry_count + 1}/{MAX_OOM_RETRIES}",
                        "progress": "0",
                    })
                    await self.redis.rpush(f"queue:{model}", task_id)
                    logger.info("Task %s re-queued (retry %d)", task_id, retry_count + 1)
                    return

            # Non-OOM error, or OOM retries exhausted
            logger.exception("Task %s failed: %s", task_id, e)
            try:
                await self.redis.hset(f"task:{task_id}", mapping={
                    "status": TaskStatus.FAILED.value,
                    "error": error_msg,
                    "completed_at": str(int(time.time())),
                })
            except Exception as redis_err:
                logger.error("Failed to mark task %s as failed in Redis: %s", task_id, redis_err)

    # Estimated time weights per ComfyUI node class (relative units).
    # Heavier weight = node takes longer = gets more progress range.
    _NODE_WEIGHTS: dict[str, int] = {
        # ── Model loaders (slow, I/O bound) ──
        "WanVideoModelLoader": 15,
        "UNETLoader": 15,
        "LoadWanVideoT5TextEncoder": 5,
        "CLIPLoader": 5,
        "WanVideoVAELoader": 2,
        "VAELoader": 2,
        "CLIPVisionLoader": 2,
        # ── Samplers (GPU heavy, dominant time) ──
        "WanVideoSampler": 25,
        "WanMoeKSamplerAdvanced": 25,
        "KSampler": 25,
        # ── Encoding / decoding ──
        "WanVideoTextEncode": 2,
        "CLIPTextEncode": 2,
        "CLIPVisionEncode": 2,
        "WanVideoImageToVideoEncode": 3,
        "WanVideoDecode": 8,
        "VAEDecode": 8,
        "PainterI2V": 5,
        # ── Post-processing ──
        "AutoLoadRifeTensorrtModel": 2,
        "AutoRifeTensorrt": 10,
        "MMAudioModelLoader": 3,
        "MMAudioFeatureUtilsLoader": 3,
        "MMAudioSampler": 10,
        "VHS_VideoCombine": 3,
        "ColorMatch": 2,
        "ImageScale": 1,
        # ── Fast / instant nodes ──
        "VRAMCleanup": 1,
        "LoadImage": 1,
        "WanVideoImageResizeToClosest": 1,
        "WanVideoLoraSelect": 1,
        "WanVideoSetLoRAs": 1,
        "Power Lora Loader (rgthree)": 1,
        "Seed (rgthree)": 1,
        "FloatConstant": 1,
        "INTConstant": 1,
        "PrimitiveFloat": 1,
        "SamplerSelector": 1,
        "SchedulerSelector": 1,
        "mxSlider": 1,
        "PathchSageAttentionKJ": 1,
        "ModelPatchTorchSettings": 1,
    }
    _NODE_WEIGHT_DEFAULT = 2

    async def _wait_with_progress(self, client: ComfyUIClient, prompt_id: str, task_id: str, timeout: float = 600) -> dict:
        overall_start = asyncio.get_event_loop().time()
        ws_url = client.base_url.replace("http://", "ws://").replace("https://", "wss://")
        try:
            import websockets

            # ── Pre-calculate node weights from workflow ──
            workflow_json = await self.redis.hget(f"task:{task_id}", "workflow")
            workflow = json.loads(workflow_json) if workflow_json else {}

            node_weights: dict[str, int] = {}
            for nid, ndata in workflow.items():
                ct = ndata.get("class_type", "") if isinstance(ndata, dict) else ""
                node_weights[nid] = self._NODE_WEIGHTS.get(ct, self._NODE_WEIGHT_DEFAULT)

            total_weight = sum(node_weights.values()) or 1

            # Progress range: 5% → 89% mapped to node weight completion
            P_START, P_END = 0.05, 0.89
            P_RANGE = P_END - P_START

            # ── State tracking ──
            high_water_progress = 0.0    # never let progress go backwards
            # For "executing" message fallback (standard ComfyUI)
            exec_completed_weight = 0.0
            exec_current_node_id = None
            exec_current_node_weight = 0
            # Unified node progress cache: {node_id: fraction 0.0-1.0}
            node_frac: dict[str, float] = {}
            ps_running_node_id: str | None = None  # last running node from progress_state

            async with websockets.connect(f"{ws_url}/ws?clientId=api-{prompt_id}") as ws:
                deadline = asyncio.get_event_loop().time() + timeout
                msg_count = 0
                last_history_check = asyncio.get_event_loop().time()
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=10)
                        msg_count += 1
                        # Skip binary messages (preview images etc.)
                        if isinstance(msg, bytes):
                            now = asyncio.get_event_loop().time()
                            if now - last_history_check >= 5:
                                last_history_check = now
                                history = await client.get_history(prompt_id)
                                if history:
                                    self._check_history_result(history, task_id, "binary msgs")
                            continue
                        data = json.loads(msg)
                        msg_type = data.get("type")
                        d = data.get("data", {})

                        # ── progress_state: rich node-level progress (custom ComfyUI) ──
                        if msg_type == "progress_state" and d.get("prompt_id") == prompt_id:
                            nodes_data = d.get("nodes", {})
                            # Update node progress cache from progress_state
                            for nid, ninfo in nodes_data.items():
                                if not isinstance(ninfo, dict):
                                    continue
                                state = ninfo.get("state", "")
                                if state == "finished":
                                    node_frac[nid] = 1.0
                                elif state == "running":
                                    ps_running_node_id = nid
                                    val = ninfo.get("value", 0)
                                    mx = ninfo.get("max", 1)
                                    # Only update if progress_state has useful info (not 0/1 placeholder)
                                    cur = node_frac.get(nid, 0)
                                    ps_frac = val / max(mx, 1) if mx else 0
                                    node_frac[nid] = max(cur, ps_frac)

                            # Calculate weighted progress from cache
                            weighted_done = sum(node_weights.get(nid, self._NODE_WEIGHT_DEFAULT) * frac
                                                for nid, frac in node_frac.items())
                            progress = round(P_START + P_RANGE * weighted_done / total_weight, 3)
                            progress = min(progress, P_END)
                            progress = max(progress, high_water_progress)
                            if progress > high_water_progress:
                                high_water_progress = progress
                                await self.redis.hset(f"task:{task_id}", "progress", str(progress))

                        # ── executing: standard ComfyUI node-by-node signal ──
                        elif msg_type == "executing" and d.get("prompt_id") == prompt_id:
                            node_id = d.get("node")
                            if node_id is None:
                                logger.info("Task %s: completion signal received via WebSocket", task_id)
                                return await client.get_history(prompt_id)

                            # Node transition: previous node finished
                            if exec_current_node_id and exec_current_node_id != node_id:
                                exec_completed_weight += node_weights.get(exec_current_node_id, self._NODE_WEIGHT_DEFAULT)
                            exec_current_node_id = node_id
                            exec_current_node_weight = node_weights.get(node_id, self._NODE_WEIGHT_DEFAULT)

                            progress = round(P_START + P_RANGE * exec_completed_weight / total_weight, 3)
                            progress = min(progress, P_END)
                            if progress > high_water_progress:
                                high_water_progress = progress
                                await self.redis.hset(f"task:{task_id}", "progress", str(progress))

                        # ── progress: step-level display info only ──
                        # Progress calculation is driven by progress_state messages.
                        # Step messages just update the step display fields.
                        elif msg_type == "progress" and d.get("prompt_id") == prompt_id:
                            step = d.get("value", 0)
                            max_step = d.get("max", 1)
                            await self.redis.hset(f"task:{task_id}", mapping={
                                "current_step": str(step),
                                "max_step": str(max_step),
                            })

                        elif msg_type in ("execution_error", "execution_interrupted"):
                            if d.get("prompt_id") == prompt_id:
                                err_msg = d.get("exception_message", "ComfyUI execution error").strip()
                                logger.error("Task %s: %s received via WebSocket: %s", task_id, msg_type, err_msg)
                                raise RuntimeError(err_msg)

                        # Periodically check history for completion (every 5s)
                        now = asyncio.get_event_loop().time()
                        if now - last_history_check >= 5:
                            last_history_check = now
                            history = await client.get_history(prompt_id)
                            if history:
                                self._check_history_result(history, task_id, "periodic")
                    except asyncio.TimeoutError:
                        # No message for 10s — check history
                        history = await client.get_history(prompt_id)
                        if history:
                            self._check_history_result(history, task_id, "idle")
                        continue
        except _HistoryReady as hr:
            return hr.history
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            logger.warning("Task %s: WebSocket failed, falling back to polling: %s", task_id, e)
        # Polling fallback — use remaining time, not full timeout (M4)
        remaining = timeout - (asyncio.get_event_loop().time() - overall_start)
        if remaining <= 0:
            raise TimeoutError(f"Prompt {prompt_id} timed out after {timeout}s (no time left for polling)")
        logger.info("Task %s: entering polling fallback for prompt %s (%.0fs remaining)", task_id, prompt_id, remaining)
        deadline = asyncio.get_event_loop().time() + remaining
        while asyncio.get_event_loop().time() < deadline:
            history = await client.get_history(prompt_id)
            if history:
                try:
                    self._check_history_result(history, task_id, "polling")
                except _HistoryReady as hr:
                    return hr.history
            await asyncio.sleep(3)
        raise TimeoutError(f"Prompt {prompt_id} timed out after {timeout}s")

    # ── Chain orchestration ──────────────────────────────────────────

    async def create_chain(self, segment_count: int, params: dict) -> str:
        chain_id = uuid.uuid4().hex
        await self.redis.hset(f"chain:{chain_id}", mapping={
            "status": "queued",
            "total_segments": str(segment_count),
            "completed_segments": "0",
            "current_segment": "0",
            "segment_task_ids": "[]",
            "final_video_url": "",
            "error": "",
            "created_at": str(int(time.time())),
            "completed_at": "",
            "params": json.dumps(params),
        })
        await self.redis.expire(f"chain:{chain_id}", TASK_EXPIRY)
        logger.info("Chain %s created with %d segments", chain_id, segment_count)
        return chain_id

    async def get_chain(self, chain_id: str) -> Optional[dict]:
        data = await self.redis.hgetall(f"chain:{chain_id}")
        if not data:
            return None
        task_ids = json.loads(data.get("segment_task_ids", "[]"))
        current_segment = int(data.get("current_segment", 0))
        completed = int(data.get("completed_segments", 0))

        # Get current task progress
        current_task_progress = 0.0
        current_task_id = None
        if task_ids and current_segment < len(task_ids):
            current_task_id = task_ids[current_segment]
            task_data = await self.get_task(current_task_id)
            if task_data:
                current_task_progress = task_data.get("progress", 0.0)

        return {
            "chain_id": chain_id,
            "status": data.get("status", "unknown"),
            "total_segments": int(data.get("total_segments", 0)),
            "completed_segments": completed,
            "current_segment": current_segment,
            "current_task_id": current_task_id,
            "current_task_progress": current_task_progress,
            "segment_task_ids": task_ids,
            "final_video_url": data.get("final_video_url") or None,
            "error": data.get("error") or None,
            "params": json.loads(data["params"]) if data.get("params") else None,
            "created_at": int(data["created_at"]) if data.get("created_at") else None,
            "completed_at": int(data["completed_at"]) if data.get("completed_at") and data["completed_at"] else None,
        }

    async def run_chain(self, chain_id: str, segments: list[dict]):
        """Start chain worker as background task."""
        task = asyncio.create_task(self._chain_worker(chain_id, segments))
        self._chain_workers[chain_id] = task
        task.add_done_callback(lambda _: self._chain_workers.pop(chain_id, None))

    async def cancel_chain(self, chain_id: str) -> bool:
        """Cancel a running chain and its current task."""
        data = await self.redis.hgetall(f"chain:{chain_id}")
        if not data:
            return False
        status = data.get("status")
        if status not in ("running", "queued"):
            return False

        # Cancel the asyncio worker task
        worker = self._chain_workers.get(chain_id)
        if worker and not worker.done():
            worker.cancel()

        # Cancel current running segment task
        task_ids_raw = data.get("segment_task_ids", "[]")
        try:
            task_ids = json.loads(task_ids_raw)
        except Exception:
            task_ids = []
        for tid in task_ids:
            task_data = await self.redis.hgetall(f"task:{tid}")
            if task_data and task_data.get("status") in ("running", "queued"):
                await self.cancel_task(tid)

        # Mark chain as failed
        await self.redis.hset(f"chain:{chain_id}", mapping={
            "status": "failed",
            "error": "Cancelled by user",
            "completed_at": str(int(time.time())),
        })
        return True

    async def _chain_worker(self, chain_id: str, segments: list[dict]):
        """Core chain loop: generate segments sequentially, concat at end.

        Story mode uses a merged workflow (single ComfyUI prompt with shared models)
        to eliminate per-segment model reload overhead.
        """
        from api.services.ffmpeg_utils import extract_last_frame, concat_videos
        from api.services.prompt_optimizer import PromptOptimizer
        import base64

        task_ids = []
        video_paths = []
        segment_filenames = []  # track local filenames for cleanup
        total = len(segments)
        optimizer = PromptOptimizer()
        original_prompt = segments[0].get("original_prompt", segments[0]["prompt"])
        auto_continue = segments[0].get("auto_continue", True)
        story_mode = segments[0].get("story_mode", False)

        # Get segment_prompts from chain params for VLM guidance
        chain_data = await self.redis.hgetall(f"chain:{chain_id}")
        chain_params = json.loads(chain_data.get("params", "{}"))
        segment_prompts = chain_params.get("segment_prompts", [])

        try:
            await self.redis.hset(f"chain:{chain_id}", "status", "running")

            # ── Merged story mode: single ComfyUI prompt with shared models ──
            # All story_mode flows go through merged path (handles I2V, T2V fallback,
            # face_reference, and cross-workflow continuation internally)
            if story_mode:
                await self._chain_worker_merged_story(
                    chain_id, segments, segment_prompts,
                )
                return

            for i, seg in enumerate(segments):
                await self.redis.hset(f"chain:{chain_id}", mapping={
                    "current_segment": str(i),
                    "segment_task_ids": json.dumps(task_ids),
                })

                model = ModelType(seg["model"])
                client = self._get_client(model.value)
                if not client:
                    raise RuntimeError(f"ComfyUI {model.value} not available")

                # ── Standard chain mode ──
                # For segments after the first, use VLM to generate continuation prompt
                if i > 0 and video_paths and auto_continue:
                    frame_path = await extract_last_frame(video_paths[-1])
                    frame_data = frame_path.read_bytes()
                    frame_b64 = base64.b64encode(frame_data).decode()

                    # VLM continuation: analyze last frame + story context + target prompt
                    prev_prompt = segments[i - 1]["prompt"]
                    target_prompt = segment_prompts[i] if i < len(segment_prompts) else seg["prompt"]
                    new_prompt = await optimizer.continue_prompt(
                        original_prompt=original_prompt,
                        frame_image_base64=frame_b64,
                        segment_index=i,
                        total_segments=total,
                        target_prompt=target_prompt,
                        previous_prompt=prev_prompt,
                    )
                    seg["prompt"] = new_prompt
                    # Store debug info
                    seg["segment_index"] = i
                    seg["target_prompt"] = target_prompt
                    seg["vlm_prompt"] = new_prompt
                    seg["final_prompt"] = new_prompt
                    logger.info("Chain %s seg %d: VLM prompt (target: %s...): %s", chain_id, i, target_prompt[:50], new_prompt[:100])

                    # Upload frame to ComfyUI
                    upload_result = await client.upload_image(frame_data, frame_path.name)
                    image_filename = upload_result.get("name", frame_path.name)

                    mode = GenerateMode.I2V
                    workflow = await asyncio.to_thread(
                        build_workflow,
                        mode=mode, model=model,
                        prompt=seg["prompt"],
                        negative_prompt=seg.get("negative_prompt", ""),
                        width=seg["width"], height=seg["height"],
                        num_frames=seg["num_frames"], fps=seg["fps"],
                        steps=seg["steps"], cfg=seg["cfg"], shift=seg["shift"],
                        seed=seg.get("seed"), loras=seg.get("loras", []),
                        scheduler=seg.get("scheduler", "unipc"),
                        model_preset=seg.get("model_preset", ""),
                        image_filename=image_filename,
                        noise_aug_strength=seg.get("noise_aug_strength", 0.05),
                        motion_amplitude=seg.get("motion_amplitude", 0.0),
                        color_match=seg.get("color_match", True),
                        color_match_method=seg.get("color_match_method", "mkl"),
                        resize_mode=seg.get("resize_mode", "crop_to_new"),
                        upscale=seg.get("upscale", False),
                        t5_preset=seg.get("t5_preset", ""),
                    )
                elif i == 0 and not seg.get("image_filename"):
                    mode = GenerateMode.T2V
                    # Store debug info for first segment
                    seg["segment_index"] = i
                    seg["target_prompt"] = segment_prompts[i] if i < len(segment_prompts) else seg["prompt"]
                    seg["final_prompt"] = seg["prompt"]
                    workflow = await asyncio.to_thread(
                        build_workflow,
                        mode=mode, model=model,
                        prompt=seg["prompt"],
                        negative_prompt=seg.get("negative_prompt", ""),
                        width=seg["width"], height=seg["height"],
                        num_frames=seg["num_frames"], fps=seg["fps"],
                        steps=seg["steps"], cfg=seg["cfg"], shift=seg["shift"],
                        seed=seg.get("seed"), loras=seg.get("loras", []),
                        scheduler=seg.get("scheduler", "unipc"),
                        model_preset=seg.get("model_preset", ""),
                        upscale=seg.get("upscale", False),
                        t5_preset=seg.get("t5_preset", ""),
                        standin_face_image=seg.get("standin_face_image") or chain_params.get("standin_face_image"),
                    )
                else:
                    mode = GenerateMode.I2V
                    image_filename = seg.get("image_filename", "")
                    # Store debug info for non-auto-continue segments
                    seg["segment_index"] = i
                    seg["target_prompt"] = segment_prompts[i] if i < len(segment_prompts) else seg["prompt"]
                    seg["final_prompt"] = seg["prompt"]
                    if i > 0 and video_paths and not auto_continue:
                        frame_path = await extract_last_frame(video_paths[-1])
                        frame_data = frame_path.read_bytes()
                        upload_result = await client.upload_image(frame_data, frame_path.name)
                        image_filename = upload_result.get("name", frame_path.name)

                    workflow = await asyncio.to_thread(
                        build_workflow,
                        mode=mode, model=model,
                        prompt=seg["prompt"],
                        negative_prompt=seg.get("negative_prompt", ""),
                        width=seg["width"], height=seg["height"],
                        num_frames=seg["num_frames"], fps=seg["fps"],
                        steps=seg["steps"], cfg=seg["cfg"], shift=seg["shift"],
                        seed=seg.get("seed"), loras=seg.get("loras", []),
                        scheduler=seg.get("scheduler", "unipc"),
                        model_preset=seg.get("model_preset", ""),
                        image_filename=image_filename,
                        noise_aug_strength=seg.get("noise_aug_strength", 0.05),
                        motion_amplitude=seg.get("motion_amplitude", 0.0),
                        color_match=seg.get("color_match", True),
                        color_match_method=seg.get("color_match_method", "mkl"),
                        resize_mode=seg.get("resize_mode", "crop_to_new"),
                        upscale=seg.get("upscale", False),
                        t5_preset=seg.get("t5_preset", ""),
                    )

                # Inject post-processing nodes (upscale, RIFE, MMAudio) for standard chain mode.
                if seg.get("enable_upscale") or seg.get("enable_interpolation") or seg.get("enable_mmaudio"):
                    workflow = await asyncio.to_thread(_inject_story_postproc, workflow, seg)

                task_id = await self.create_task(mode, model, workflow, params=seg, chain_id=chain_id)
                task_ids.append(task_id)
                await self.redis.hset(f"chain:{chain_id}", "segment_task_ids", json.dumps(task_ids))

                # Wait for this segment to complete (raises on failure/timeout)
                video_path = await self._wait_for_task_completion(task_id, timeout=1800)
                video_paths.append(video_path)
                segment_filenames.append(video_path.name)

                await self.redis.hset(f"chain:{chain_id}", "completed_segments", str(i + 1))
                logger.info("Chain %s: segment %d/%d completed", chain_id, i + 1, total)

            # All segments done — concatenate
            if len(video_paths) > 1:
                fps = segments[0].get("fps", 24)
                transition = segments[0].get("transition", "none")
                final_path = await concat_videos(video_paths, fps, transition=transition)
                final_data = final_path.read_bytes()
                ext = final_path.suffix.lstrip(".")
                result = await storage.save_video(final_data, ext)
                final_url = result if COS_ENABLED else f"{VIDEO_BASE_URL}/{result}"
                # Clean up concat temp file if it's different from saved
                if final_path.name not in segment_filenames:
                    storage.cleanup_local(final_path.name)
            else:
                task_data = await self.get_task(task_ids[0])
                final_url = task_data.get("video_url", "") if task_data else ""

            # Clean up local segment files (COS already has them)
            if COS_ENABLED:
                for fn in segment_filenames:
                    storage.cleanup_local(fn)

            await self.redis.hset(f"chain:{chain_id}", mapping={
                "status": "completed",
                "final_video_url": final_url,
                "completed_at": str(int(time.time())),
                "segment_task_ids": json.dumps(task_ids),
            })
            logger.info("Chain %s completed: %s", chain_id, final_url)

        except BaseException as e:
            # C3: catch CancelledError (BaseException) alongside regular exceptions
            error_msg = "Cancelled" if isinstance(e, asyncio.CancelledError) else str(e)
            try:
                current_seg = int(await self.redis.hget(f"chain:{chain_id}", "current_segment") or 0)
                logger.exception("Chain %s failed at segment %d: %s", chain_id, current_seg, error_msg)
                completed = int(await self.redis.hget(f"chain:{chain_id}", "completed_segments") or 0)
                status = "partial" if completed > 0 else "failed"
                await self.redis.hset(f"chain:{chain_id}", mapping={
                    "status": status,
                    "error": error_msg,
                    "completed_at": str(int(time.time())),
                    "segment_task_ids": json.dumps(task_ids),
                })
            except Exception as redis_err:
                logger.error("Failed to mark chain %s as failed in Redis: %s", chain_id, redis_err)
            # Re-raise CancelledError to not swallow cancellation
            if isinstance(e, asyncio.CancelledError):
                raise

    async def _chain_worker_merged_story(
        self, chain_id: str, segments: list[dict], segment_prompts: list[str],
    ):
        """Merged story mode: build one ComfyUI workflow with shared models for all segments.

        Eliminates per-segment model reload overhead (~70s each). All segments share
        UNETLoader HIGH/LOW, VAELoader, and CLIPLoader nodes. Segment inter-dependencies
        (previous_video) are wired inside the workflow DAG.

        VLM continuation is NOT available in merged mode (segments 1+ haven't been
        generated yet at submission time). Users must provide per-segment prompts.
        """
        from api.services.ffmpeg_utils import concat_videos

        seg0 = segments[0]
        total = len(segments)
        model = ModelType(seg0["model"])
        client = self._get_client(model.value)
        if not client:
            raise RuntimeError(f"ComfyUI {model.value} not available")

        # Apply per-segment prompts from segment_prompts list
        for i, seg in enumerate(segments):
            if i < len(segment_prompts) and segment_prompts[i]:
                seg["prompt"] = segment_prompts[i]
            seg["segment_index"] = i
            seg["final_prompt"] = seg["prompt"]

        # Check if this is face_reference mode
        # If so, use T2V workflow instead of merged story workflow
        image_filename = seg0.get("image_filename", "")
        face_image_filename = seg0.get("face_image_filename", "")
        image_mode = seg0.get("image_mode", "first_frame")

        from api.models.schemas import FaceSwapConfig

        if image_mode == "face_reference" and total == 1:
            # Single segment face_reference mode: use I2V workflow (PainterI2V)
            logger.info("Chain %s: single segment face_reference mode, using I2V workflow", chain_id)

            workflow = await asyncio.to_thread(
                build_story_workflow,
                is_first_segment=True,
                prompt=seg0["prompt"],
                negative_prompt=seg0.get("negative_prompt", ""),
                width=seg0["width"],
                height=seg0["height"],
                num_frames=seg0["num_frames"],
                seed=seg0.get("seed"),
                shift=seg0.get("shift", 8.0),
                cfg=seg0.get("cfg", 1.0),
                steps=seg0.get("steps", 20),
                motion_amplitude=seg0.get("motion_amplitude", 1.15),
                motion_frames=seg0.get("motion_frames", 5),
                boundary=seg0.get("boundary", 0.9),
                image_filename=image_filename or face_image_filename,
                model_preset=seg0.get("model_preset", "nsfw_v2"),
                clip_preset=seg0.get("clip_preset", "nsfw"),
                fps=seg0.get("fps", 16),
                upscale=seg0.get("upscale", False),
                loras=seg0.get("loras", []),
            )

            # Inject post-processing if needed
            if seg0.get("enable_upscale") or seg0.get("enable_interpolation") or seg0.get("enable_mmaudio"):
                workflow = await asyncio.to_thread(_inject_story_postproc, workflow, seg0)

            # Create task and wait for completion
            task_id = await self.create_task(
                GenerateMode.I2V, model, workflow,
                params={"face_reference_i2v": True},
                chain_id=chain_id,
            )
            task_ids = [task_id]
            await self.redis.hset(f"chain:{chain_id}", mapping={
                "segment_task_ids": json.dumps(task_ids),
                "current_segment": "0",
            })

            video_path = await self._wait_for_task_completion(task_id, timeout=1800)
            task_data = await self.redis.hgetall(f"task:{task_id}")
            video_url = task_data.get("video_url", "")

            await self.redis.hset(f"chain:{chain_id}", mapping={
                "status": "completed",
                "completed_at": str(int(asyncio.get_event_loop().time())),
                "final_video_url": video_url,
            })
            return

        # Extract parent_video_filename for cross-workflow continuation
        parent_video_fn = seg0.get("parent_video_filename", "")

        # T2V fallback: no start image and no parent video — use standard T2V workflow
        if not seg0.get("image_filename") and not parent_video_fn and not face_image_filename:
            logger.info("Chain %s: no start image, using T2V fallback", chain_id)
            workflow = await asyncio.to_thread(
                build_workflow,
                mode=GenerateMode.T2V,
                model=model,
                prompt=seg0["prompt"],
                negative_prompt=seg0.get("negative_prompt", ""),
                width=seg0["width"], height=seg0["height"],
                num_frames=seg0["num_frames"], fps=seg0["fps"],
                steps=seg0["steps"], cfg=seg0["cfg"], shift=seg0["shift"],
                seed=seg0.get("seed"), loras=seg0.get("loras", []),
                scheduler=seg0.get("scheduler", "unipc"),
                model_preset=seg0.get("model_preset", ""),
                upscale=seg0.get("upscale", False),
                t5_preset=seg0.get("t5_preset", ""),
            )
            # Inject lossless last frame save (for cross-workflow continuation)
            workflow = _inject_lossless_frame_save(workflow)
            # Inject post-processing nodes if needed
            if seg0.get("enable_upscale") or seg0.get("enable_interpolation") or seg0.get("enable_mmaudio"):
                workflow = await asyncio.to_thread(_inject_story_postproc, workflow, seg0)

            task_id = await self.create_task(
                GenerateMode.T2V, model, workflow,
                params={"story_t2v_fallback": True},
                chain_id=chain_id,
            )
            task_ids = [task_id]
            await self.redis.hset(f"chain:{chain_id}", mapping={
                "segment_task_ids": json.dumps(task_ids),
                "current_segment": "0",
            })

            video_path = await self._wait_for_task_completion(task_id, timeout=1800)
            task_data = await self.redis.hgetall(f"task:{task_id}")
            video_url = task_data.get("video_url", "")

            # Retrieve lossless last frame from SaveImage node
            t2v_lossless_url = None
            try:
                prompt_id = task_data.get("prompt_id", "")
                comfyui_url = task_data.get("comfyui_url", "")
                t2v_client = self._get_client_by_url(model.value, comfyui_url) if comfyui_url else client
                if prompt_id:
                    output_images = await t2v_client.get_output_images(prompt_id)
                    lastframe_images = [
                        f for f in output_images
                        if f.get("filename", "").startswith("wan22_story_lastframe")
                    ]
                    if lastframe_images:
                        img = lastframe_images[-1]
                        img_data = await t2v_client.download_file(
                            img["filename"], img.get("subfolder", ""), img.get("type", "output"),
                        )
                        _, frame_url = await storage.save_upload(img_data, img["filename"])
                        t2v_lossless_url = frame_url
                        logger.info("Chain %s: saved lossless last frame (T2V): %s", chain_id, t2v_lossless_url)
            except Exception as e:
                logger.warning("Chain %s: failed to retrieve lossless last frame (T2V): %s", chain_id, e)

            t2v_mapping = {
                "status": "completed",
                "completed_segments": str(total),
                "completed_at": str(int(time.time())),
                "final_video_url": video_url,
                "segment_task_ids": json.dumps(task_ids),
            }
            if t2v_lossless_url:
                t2v_mapping["lossless_last_frame_url"] = t2v_lossless_url
            await self.redis.hset(f"chain:{chain_id}", mapping=t2v_mapping)
            logger.info("Chain %s (T2V fallback) completed: %s", chain_id, video_url)
            return

        logger.info("Chain %s: building merged story workflow for %d segments", chain_id, total)

        # Extract face_image_filename from segment 0 if present
        face_swap_strength = seg0.get("face_swap_strength", 1.0)

        # Build single merged workflow (run in thread to avoid blocking event loop)
        workflow = await asyncio.to_thread(build_merged_story_workflow,
            segments=segments,
            width=seg0["width"],
            height=seg0["height"],
            shift=seg0.get("shift", 8.0),
            cfg=seg0.get("cfg", 1.0),
            steps=seg0.get("steps", 20),
            motion_amplitude=seg0.get("motion_amplitude", 1.15),
            motion_frames=seg0.get("motion_frames", 5),
            boundary=seg0.get("boundary", 0.9),
            image_filename=seg0.get("image_filename", ""),
            model_preset=seg0.get("model_preset", "nsfw_v2"),
            clip_preset=seg0.get("clip_preset", "nsfw"),
            fps=seg0.get("fps", 16),
            upscale=seg0.get("upscale", False),
            loras=None,
            match_image_ratio=seg0.get("match_image_ratio", False),
            enable_upscale=seg0.get("enable_upscale", False),
            upscale_model=seg0.get("upscale_model", "4x_foolhardy_Remacri"),
            upscale_resize=seg0.get("upscale_resize", "2x"),
            enable_interpolation=seg0.get("enable_interpolation", False),
            interpolation_multiplier=seg0.get("interpolation_multiplier", 2),
            interpolation_profile=seg0.get("interpolation_profile", "small"),
            enable_mmaudio=seg0.get("enable_mmaudio", False),
            mmaudio_prompt=seg0.get("mmaudio_prompt", ""),
            mmaudio_negative_prompt=seg0.get("mmaudio_negative_prompt", ""),
            mmaudio_steps=seg0.get("mmaudio_steps", 12),
            mmaudio_cfg=seg0.get("mmaudio_cfg", 4.5),
            face_image_filename=face_image_filename,
            face_swap_strength=face_swap_strength,
            parent_video_filename=parent_video_fn,
            initial_ref_filename=seg0.get("initial_ref_filename", ""),
        )

        # Create a single task for the entire merged workflow
        task_id = await self.create_task(
            GenerateMode.I2V, model, workflow,
            params={"merged_story": True, "segment_count": total},
            chain_id=chain_id,
        )
        task_ids = [task_id]
        await self.redis.hset(f"chain:{chain_id}", mapping={
            "segment_task_ids": json.dumps(task_ids),
            "current_segment": "0",
        })

        # Wait for the single merged prompt to complete
        # (timeout scales with segment count: ~120s base + ~30s per segment)
        merged_timeout = 120 + total * 60
        video_path = await self._wait_for_task_completion(task_id, timeout=max(merged_timeout, 1800))

        # With ImageBatchMulti optimization, merged workflow now outputs only 1 video
        # (all segments merged in ComfyUI, no need for external ffmpeg concat)
        task_data = await self.redis.hgetall(f"task:{task_id}")
        prompt_id = task_data.get("prompt_id", "")
        if not prompt_id:
            raise RuntimeError("No prompt_id found for merged story task")

        # Use the specific client that processed this task
        comfyui_url = task_data.get("comfyui_url", "")
        if comfyui_url:
            client = self._get_client_by_url(model.value, comfyui_url)

        output_files = await client.get_output_files_ordered(prompt_id)

        # Retrieve lossless last frame from SaveImage node (for cross-workflow continuation)
        lossless_frame_url = None
        try:
            output_images = await client.get_output_images(prompt_id)
            lastframe_images = [
                f for f in output_images
                if f.get("filename", "").startswith("wan22_story_lastframe")
            ]
            if lastframe_images:
                img = lastframe_images[-1]
                img_data = await client.download_file(
                    img["filename"], img.get("subfolder", ""), img.get("type", "output"),
                )
                _, frame_url = await storage.save_upload(img_data, img["filename"])
                lossless_frame_url = frame_url
                logger.info("Chain %s: saved lossless last frame: %s", chain_id, lossless_frame_url)
        except Exception as e:
            logger.warning("Chain %s: failed to retrieve lossless last frame: %s", chain_id, e)

        if not output_files:
            # Post-processing nodes may have failed (e.g. RIFE TRT), but the task
            # itself may have completed with a video from the preview node.
            # Fall back to the task's already-saved video_url if available.
            task_video_url = task_data.get("video_url", "")
            if task_video_url:
                logger.warning("Chain %s: no output files from final node, using task video_url fallback", chain_id)
                fallback_mapping = {
                    "status": "completed",
                    "completed_segments": str(total),
                    "final_video_url": task_video_url,
                    "completed_at": str(int(time.time())),
                    "segment_task_ids": json.dumps(task_ids),
                }
                if lossless_frame_url:
                    fallback_mapping["lossless_last_frame_url"] = lossless_frame_url
                await self.redis.hset(f"chain:{chain_id}", mapping=fallback_mapping)
                logger.info("Chain %s (merged story, fallback) completed: %s", chain_id, task_video_url)
                return
            raise RuntimeError("No output files from merged story workflow")

        logger.info("Chain %s: merged workflow produced %d output files", chain_id, len(output_files))

        await self.redis.hset(f"chain:{chain_id}", "completed_segments", str(total))

        # The optimized workflow outputs a single merged video (from node 510)
        # No need to concatenate multiple videos anymore
        if len(output_files) == 1:
            # Single merged video - use it directly
            f = output_files[0]
            data = await client.download_file(
                f["filename"], f.get("subfolder", ""), f.get("type", "output"),
            )
            ext = f["filename"].rsplit(".", 1)[-1] if "." in f["filename"] else "mp4"
            result = await storage.save_video(data, ext)
            final_url = result if COS_ENABLED else f"{VIDEO_BASE_URL}/{result}"
            logger.info("Chain %s: using single merged video: %s", chain_id, f["filename"])
        else:
            # Fallback: multiple videos (old behavior, shouldn't happen with optimization)
            logger.warning("Chain %s: got %d videos, expected 1 (falling back to concat)",
                          chain_id, len(output_files))
            video_paths = []
            segment_filenames = []
            for f in output_files:
                data = await client.download_file(
                    f["filename"], f.get("subfolder", ""), f.get("type", "output"),
                )
                ext = f["filename"].rsplit(".", 1)[-1] if "." in f["filename"] else "mp4"
                result = await storage.save_video(data, ext)
                seg_url = result if COS_ENABLED else f"{VIDEO_BASE_URL}/{result}"
                seg_path = await storage.get_video_path_from_url(seg_url)
                if seg_path and seg_path.exists():
                    video_paths.append(seg_path)
                    segment_filenames.append(seg_path.name)

            if len(video_paths) > 1:
                fps = seg0.get("fps", 16)
                transition = seg0.get("transition", "none")
                final_path = await concat_videos(video_paths, fps, transition=transition)
                final_data = final_path.read_bytes()
                ext = final_path.suffix.lstrip(".")
                result = await storage.save_video(final_data, ext)
                final_url = result if COS_ENABLED else f"{VIDEO_BASE_URL}/{result}"
                if final_path.name not in segment_filenames:
                    storage.cleanup_local(final_path.name)
            elif video_paths:
                task_result = await self.get_task(task_id)
                final_url = task_result.get("video_url", "") if task_result else ""
            else:
                raise RuntimeError("No segment videos downloaded from merged workflow")

            # Clean up local segment files
            if COS_ENABLED:
                for fn in segment_filenames:
                    storage.cleanup_local(fn)

        completion_mapping = {
            "status": "completed",
            "final_video_url": final_url,
            "completed_at": str(int(time.time())),
            "segment_task_ids": json.dumps(task_ids),
        }
        if lossless_frame_url:
            completion_mapping["lossless_last_frame_url"] = lossless_frame_url
        await self.redis.hset(f"chain:{chain_id}", mapping=completion_mapping)
        logger.info("Chain %s (merged story) completed: %s", chain_id, final_url)

    async def _wait_for_task_completion(self, task_id: str, timeout: float = 1800) -> Optional['Path']:
        """Poll Redis until task completes. Returns local video path.

        Raises TimeoutError on timeout, RuntimeError on task failure.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            data = await self.redis.hgetall(f"task:{task_id}")
            if not data:
                raise RuntimeError(f"Task {task_id} not found in Redis")
            status = data.get("status")
            if status == TaskStatus.COMPLETED.value:
                video_url = data.get("video_url", "")
                return await storage.get_video_path_from_url(video_url)
            if status == TaskStatus.FAILED.value:
                error = data.get("error", "Unknown error")
                raise RuntimeError(f"Task {task_id} failed: {error}")
            await asyncio.sleep(3)
        raise TimeoutError(f"Task {task_id} timed out after {timeout}s")

    def _check_history_result(self, history: dict, task_id: str, source: str):
        """Check ComfyUI history and return/raise appropriately.

        Returns history if outputs are available.
        Raises RuntimeError on error or success-with-empty-outputs.
        Does nothing if execution is still in progress.
        """
        if history.get("outputs") and any(history["outputs"].values()):
            logger.info("Task %s: completion detected via %s history check", task_id, source)
            raise _HistoryReady(history)
        status = history.get("status", {})
        status_str = status.get("status_str", "")
        if status_str == "error":
            raise RuntimeError(self._extract_error(status))
        if status.get("completed"):
            # ComfyUI finished (success) but produced no outputs
            raise RuntimeError("ComfyUI execution completed but produced no output files")

    @staticmethod
    def _extract_error(status: dict) -> str:
        for msg in status.get("messages", []):
            if isinstance(msg, list) and len(msg) >= 2 and msg[0] in ("execution_error", "execution_interrupted"):
                return msg[1].get("exception_message", "ComfyUI execution error").strip()
        return "ComfyUI execution failed"
