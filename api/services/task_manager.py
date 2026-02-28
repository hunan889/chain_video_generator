import asyncio
import json
import logging
import uuid
from typing import Optional
import redis.asyncio as aioredis
from api.config import REDIS_URL, COMFYUI_URLS, VIDEO_BASE_URL, TASK_EXPIRY
from api.models.enums import TaskStatus, ModelType, GenerateMode
from api.models.schemas import GenerateRequest, GenerateI2VRequest
from api.services.comfyui_client import ComfyUIClient
from api.services.workflow_builder import build_workflow
from api.services import storage

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self._worker_redis: Optional[aioredis.Redis] = None
        self.clients: dict[str, ComfyUIClient] = {}
        self._workers: list[asyncio.Task] = []

    async def start(self):
        self.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        self._worker_redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        for model_key, url in COMFYUI_URLS.items():
            self.clients[model_key] = ComfyUIClient(url)
        # Recover orphan running tasks (from previous crash/restart)
        await self._recover_orphan_tasks()
        # Start worker tasks for each model
        for model_key in COMFYUI_URLS:
            task = asyncio.create_task(self._worker_loop(model_key))
            self._workers.append(task)
        logger.info("TaskManager started with workers: %s", list(COMFYUI_URLS.keys()))

    async def stop(self):
        for w in self._workers:
            w.cancel()
        for client in self.clients.values():
            await client.close()
        if self.redis:
            await self.redis.close()
        if self._worker_redis:
            await self._worker_redis.close()

    async def redis_alive(self) -> bool:
        try:
            return await self.redis.ping()
        except Exception:
            return False

    async def create_task(self, mode: GenerateMode, model: ModelType, workflow: dict, params: Optional[dict] = None) -> str:
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
        await self.redis.hset(f"task:{task_id}", mapping=task_data)
        await self.redis.expire(f"task:{task_id}", TASK_EXPIRY)
        await self.redis.rpush(f"queue:{model.value}", task_id)
        logger.info("Task %s created for %s/%s", task_id, mode.value, model.value)
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
        return {
            "task_id": task_id,
            "status": data.get("status", "unknown"),
            "mode": data.get("mode") or None,
            "model": data.get("model") or None,
            "progress": float(data.get("progress", 0)),
            "video_url": data.get("video_url") or None,
            "error": data.get("error") or None,
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
            client = self.clients.get(model)
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
        """List all tasks from Redis."""
        tasks = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="task:*", count=100)
            for key in keys:
                task_id = key.split(":", 1)[1]
                data = await self.redis.hgetall(key)
                if data:
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
                    client = self.clients.get(model_key) if model_key else None

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
            local_filename = storage.save_video(data, ext)
            video_url = f"{VIDEO_BASE_URL}/{local_filename}"
            await self.redis.hset(f"task:{task_id}", mapping={
                "status": TaskStatus.COMPLETED.value,
                "progress": "1.0",
                "video_url": video_url,
                "completed_at": str(int(__import__('time').time())),
            })
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

    async def _worker_loop(self, model_key: str):
        queue_name = f"queue:{model_key}"
        client = self.clients[model_key]
        logger.info("Worker for %s started", model_key)
        while True:
            try:
                result = await self._worker_redis.blpop(queue_name, timeout=5)
                if result is None:
                    continue
                _, task_id = result
                await self._process_task(task_id, client)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Worker %s error: %s", model_key, e)
                await asyncio.sleep(2)

    async def _process_task(self, task_id: str, client: ComfyUIClient):
        try:
            await self.redis.hset(f"task:{task_id}", "status", TaskStatus.RUNNING.value)
            workflow_json = await self.redis.hget(f"task:{task_id}", "workflow")
            workflow = json.loads(workflow_json)

            # Submit to ComfyUI
            prompt_id = await client.queue_prompt(workflow)
            await self.redis.hset(f"task:{task_id}", mapping={
                "progress": "0.05",
                "prompt_id": prompt_id,
            })

            # Wait for completion with progress updates
            history = await self._wait_with_progress(client, prompt_id, task_id, timeout=1800)
            await self.redis.hset(f"task:{task_id}", "progress", "0.9")

            # Download output files
            output_files = await client.get_output_files(prompt_id)
            if not output_files:
                raise RuntimeError("No output files generated")

            f = output_files[0]
            data = await client.download_file(
                f["filename"], f.get("subfolder", ""), f.get("type", "output")
            )
            ext = f["filename"].rsplit(".", 1)[-1] if "." in f["filename"] else "mp4"
            local_filename = storage.save_video(data, ext)
            video_url = f"{VIDEO_BASE_URL}/{local_filename}"

            await self.redis.hset(f"task:{task_id}", mapping={
                "status": TaskStatus.COMPLETED.value,
                "progress": "1.0",
                "video_url": video_url,
                "completed_at": str(int(__import__('time').time())),
            })
            logger.info("Task %s completed: %s", task_id, video_url)

        except Exception as e:
            logger.exception("Task %s failed: %s", task_id, e)
            await self.redis.hset(f"task:{task_id}", mapping={
                "status": TaskStatus.FAILED.value,
                "error": str(e),
                "completed_at": str(int(__import__('time').time())),
            })

    async def _wait_with_progress(self, client: ComfyUIClient, prompt_id: str, task_id: str, timeout: float = 600) -> dict:
        ws_url = client.base_url.replace("http://", "ws://").replace("https://", "wss://")
        try:
            import websockets
            async with websockets.connect(f"{ws_url}/ws?clientId=api-{prompt_id}") as ws:
                deadline = asyncio.get_event_loop().time() + timeout
                ws_idle_count = 0
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=10)
                        ws_idle_count = 0
                        data = json.loads(msg)
                        msg_type = data.get("type")
                        d = data.get("data", {})
                        if msg_type == "progress" and d.get("prompt_id") == prompt_id:
                            step = d.get("value", 0)
                            max_step = d.get("max", 1)
                            progress = round(0.05 + 0.85 * step / max(max_step, 1), 3)
                            await self.redis.hset(f"task:{task_id}", "progress", str(progress))
                        elif msg_type == "executing":
                            if d.get("prompt_id") == prompt_id and d.get("node") is None:
                                return await client.get_history(prompt_id)
                    except asyncio.TimeoutError:
                        ws_idle_count += 1
                        # Every 3 idle cycles, check history directly (catches already-finished/errored prompts)
                        if ws_idle_count >= 3:
                            ws_idle_count = 0
                            history = await client.get_history(prompt_id)
                            if history:
                                if history.get("outputs") and any(history["outputs"].values()):
                                    return history
                                status = history.get("status", {})
                                if status.get("status_str") == "error":
                                    raise RuntimeError(self._extract_error(status))
                        continue
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            logger.warning("WebSocket progress failed, falling back to polling: %s", e)
        # Polling fallback
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            history = await client.get_history(prompt_id)
            if history:
                if history.get("outputs") and any(history["outputs"].values()):
                    return history
                status = history.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(self._extract_error(status))
            await asyncio.sleep(3)
        raise TimeoutError(f"Prompt {prompt_id} timed out after {timeout}s")

    @staticmethod
    def _extract_error(status: dict) -> str:
        for msg in status.get("messages", []):
            if isinstance(msg, list) and len(msg) >= 2 and msg[0] in ("execution_error", "execution_interrupted"):
                return msg[1].get("exception_message", "ComfyUI execution error").strip()
        return "ComfyUI execution failed"
