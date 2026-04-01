"""TaskGateway — pure Redis data operations for tasks and chains.

This module handles all task/chain CRUD against Redis. It has NO dependency
on ComfyUI, WorkflowBuilder, or any GPU-side component. Both the API gateway
and GPU worker can use this to read/write task state.
"""

import json
import logging
import time
import uuid
from typing import Optional

from shared.enums import GenerateMode, ModelType, TaskStatus
from shared.redis_keys import chain_key, queue_key, task_key

logger = logging.getLogger(__name__)


class TaskGateway:
    """Redis-backed task and chain data operations.

    Constructed with an async Redis connection and task expiry config.
    Optionally holds a ``task_store`` (MySQL-backed) for persistent writes.
    """

    def __init__(self, redis, task_expiry: int = 86400) -> None:
        self.redis = redis
        self.task_expiry = task_expiry
        self.task_store = None  # set by main.py lifespan when available

    async def redis_alive(self) -> bool:
        try:
            return await self.redis.ping()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    async def create_task(
        self,
        mode: GenerateMode,
        model: ModelType,
        workflow: dict,
        params: Optional[dict] = None,
        chain_id: Optional[str] = None,
    ) -> str:
        task_id = uuid.uuid4().hex
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
        await self.redis.hset(task_key(task_id), mapping=task_data)
        await self.redis.expire(task_key(task_id), self.task_expiry)
        await self.redis.rpush(queue_key(model.value), task_id)
        logger.info(
            "Task %s created for %s/%s%s",
            task_id, mode.value, model.value,
            f" (chain {chain_id})" if chain_id else "",
        )

        # Best-effort MySQL persistent write (skip sub-tasks with chain_id —
        # workflow engine handles MySQL for those via the parent workflow record)
        if self.task_store is not None and not chain_id:
            try:
                from shared.enums import category_for_mode
                await self.task_store.create(
                    task_id=task_id,
                    task_type=mode.value,
                    category=category_for_mode(mode).value,
                    prompt=params.get("prompt") if params else None,
                    model=model.value,
                    params=params,
                    chain_id=chain_id,
                )
            except Exception:
                logger.warning("MySQL write failed for task %s", task_id, exc_info=True)

        return task_id

    async def get_task(self, task_id: str) -> Optional[dict]:
        data = await self.redis.hgetall(task_key(task_id))
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
            "last_frame_url": data.get("last_frame_url") or None,
            "error": data.get("error") or None,
            "retry_count": int(data.get("retry_count", 0)),
            "params": params,
            "created_at": int(data["created_at"]) if data.get("created_at") else None,
            "completed_at": int(data["completed_at"]) if data.get("completed_at") else None,
        }

    async def cancel_queued_task(self, task_id: str) -> bool:
        """Cancel a queued task. Returns False if not found or not queued."""
        data = await self.redis.hgetall(task_key(task_id))
        if not data:
            return False
        status = data.get("status")
        if status != TaskStatus.QUEUED.value:
            return False
        model = data.get("model", "")
        await self.redis.lrem(queue_key(model), 0, task_id)
        await self.redis.hset(task_key(task_id), mapping={
            "status": TaskStatus.FAILED.value,
            "error": "Cancelled by user",
            "completed_at": str(int(time.time())),
        })
        return True

    async def list_tasks(self) -> list[dict]:
        """List all tasks, excluding chain segment tasks."""
        tasks = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="task:*", count=100)
            for key in keys:
                tid = key.split(":", 1)[1]
                data = await self.redis.hgetall(key)
                if not data or data.get("chain_id"):
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
                    "task_id": tid,
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
        order = {"running": 0, "queued": 1, "completed": 2, "failed": 3}
        tasks.sort(key=lambda t: (order.get(t["status"], 9), -(t.get("created_at") or 0)))
        return tasks

    # ------------------------------------------------------------------
    # Task status updates (used by Worker)
    # ------------------------------------------------------------------

    async def mark_task_running(self, task_id: str, comfyui_url: str = "", prompt_id: str = "") -> None:
        mapping = {"status": TaskStatus.RUNNING.value}
        if comfyui_url:
            mapping["comfyui_url"] = comfyui_url
        if prompt_id:
            mapping["prompt_id"] = prompt_id
        await self.redis.hset(task_key(task_id), mapping=mapping)

        if self.task_store is not None:
            try:
                await self.task_store.update_status(task_id, TaskStatus.RUNNING.value)
            except Exception:
                logger.warning("MySQL status update failed for task %s", task_id, exc_info=True)

    async def mark_task_completed(
        self, task_id: str,
        video_url: str = "",
        last_frame_url: str = "",
    ) -> None:
        mapping = {
            "status": TaskStatus.COMPLETED.value,
            "progress": "1.0",
            "completed_at": str(int(time.time())),
        }
        if video_url:
            mapping["video_url"] = video_url
        if last_frame_url:
            mapping["last_frame_url"] = last_frame_url
        await self.redis.hset(task_key(task_id), mapping=mapping)

        if self.task_store is not None:
            try:
                await self.task_store.update_status(
                    task_id, TaskStatus.COMPLETED.value, progress=1.0,
                )
                await self.task_store.set_result(
                    task_id,
                    result_url=video_url or None,
                    thumbnail_url=last_frame_url or None,
                )
            except Exception:
                logger.warning("MySQL status update failed for task %s", task_id, exc_info=True)

    async def mark_task_failed(self, task_id: str, error: str = "") -> None:
        await self.redis.hset(task_key(task_id), mapping={
            "status": TaskStatus.FAILED.value,
            "error": error,
            "completed_at": str(int(time.time())),
        })

        if self.task_store is not None:
            try:
                await self.task_store.update_status(
                    task_id, TaskStatus.FAILED.value, error=error or None,
                )
            except Exception:
                logger.warning("MySQL status update failed for task %s", task_id, exc_info=True)

    async def update_task_progress(self, task_id: str, progress: float) -> None:
        # Monotonic: only update if new progress > current progress
        current = await self.redis.hget(task_key(task_id), "progress")
        if current is not None:
            try:
                if float(current) >= progress:
                    return
            except (ValueError, TypeError):
                pass
        await self.redis.hset(task_key(task_id), mapping={"progress": str(progress)})

        if self.task_store is not None:
            try:
                await self.task_store.update_status(
                    task_id, TaskStatus.RUNNING.value, progress=progress,
                )
            except Exception:
                logger.warning("MySQL progress update failed for task %s", task_id, exc_info=True)

    # ------------------------------------------------------------------
    # Chain CRUD
    # ------------------------------------------------------------------

    async def create_chain(self, segment_count: int, params: dict) -> str:
        chain_id = uuid.uuid4().hex
        await self.redis.hset(chain_key(chain_id), mapping={
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
        await self.redis.expire(chain_key(chain_id), self.task_expiry)
        logger.info("Chain %s created with %d segments", chain_id, segment_count)
        return chain_id

    async def get_chain(self, chain_id: str) -> Optional[dict]:
        data = await self.redis.hgetall(chain_key(chain_id))
        if not data:
            return None
        task_ids = json.loads(data.get("segment_task_ids", "[]"))
        current_segment = int(data.get("current_segment", 0))
        completed = int(data.get("completed_segments", 0))

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
                cid = parts[1]
                chain = await self.get_chain(cid)
                if chain:
                    chains.append(chain)
            if cursor == 0:
                break
        order = {"running": 0, "queued": 1, "completed": 2, "partial": 3, "failed": 4}
        chains.sort(key=lambda c: (order.get(c["status"], 9), -(c.get("created_at") or 0)))
        return chains
