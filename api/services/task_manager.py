import asyncio
import json
import logging
import time
import uuid
from typing import Optional
import redis.asyncio as aioredis
from api.config import REDIS_URL, COMFYUI_URLS, VIDEO_BASE_URL, TASK_EXPIRY, COS_ENABLED
from api.models.enums import TaskStatus, ModelType, GenerateMode
from api.models.schemas import GenerateRequest, GenerateI2VRequest
from api.services.comfyui_client import ComfyUIClient
from api.services.workflow_builder import build_workflow, build_story_workflow, build_merged_story_workflow, _inject_story_postproc
from api.services import storage

logger = logging.getLogger(__name__)


class _HistoryReady(Exception):
    """Internal signal: ComfyUI history has outputs ready."""
    def __init__(self, history: dict):
        self.history = history


class TaskManager:
    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self._worker_redis: Optional[aioredis.Redis] = None
        self.clients: dict[str, ComfyUIClient] = {}
        self._workers: list[asyncio.Task] = []
        self._chain_workers: dict[str, asyncio.Task] = {}  # chain_id -> asyncio.Task

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
                        logger.info("Extracted last frame for task %s: %s", task_id, last_frame_url)
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
                msg_count = 0
                last_history_check = asyncio.get_event_loop().time()
                # Multi-stage progress tracking
                total_steps = 0       # sum of max_steps across all stages
                completed_steps = 0   # steps finished in previous stages
                current_max = 0       # max_step of current stage
                last_step = -1        # last step value seen (detect stage reset)
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=10)
                        msg_count += 1
                        # Skip binary messages (preview images etc.)
                        if isinstance(msg, bytes):
                            # Periodically check history even when receiving binary messages
                            now = asyncio.get_event_loop().time()
                            if now - last_history_check >= 30:
                                last_history_check = now
                                history = await client.get_history(prompt_id)
                                if history:
                                    self._check_history_result(history, task_id, "binary msgs")
                            continue
                        data = json.loads(msg)
                        msg_type = data.get("type")
                        d = data.get("data", {})
                        if msg_type == "progress" and d.get("prompt_id") == prompt_id:
                            step = d.get("value", 0)
                            max_step = d.get("max", 1)
                            # Detect new stage: step resets or new max_step appears
                            if max_step != current_max or (step < last_step and step <= 1):
                                if current_max > 0:
                                    completed_steps += current_max
                                current_max = max_step
                                total_steps = completed_steps + max_step
                            last_step = step
                            overall = completed_steps + step
                            progress = round(0.05 + 0.85 * overall / max(total_steps, 1), 3)
                            progress = min(progress, 0.89)
                            await self.redis.hset(f"task:{task_id}", "progress", str(progress))
                        elif msg_type == "executing":
                            if d.get("prompt_id") == prompt_id and d.get("node") is None:
                                logger.info("Task %s: completion signal received via WebSocket", task_id)
                                return await client.get_history(prompt_id)
                        elif msg_type in ("execution_error", "execution_interrupted"):
                            if d.get("prompt_id") == prompt_id:
                                err_msg = d.get("exception_message", "ComfyUI execution error").strip()
                                logger.error("Task %s: %s received via WebSocket: %s", task_id, msg_type, err_msg)
                                raise RuntimeError(err_msg)
                        # Periodically check history for completion (every 30s)
                        now = asyncio.get_event_loop().time()
                        if now - last_history_check >= 30:
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
        # Polling fallback
        logger.info("Task %s: entering polling fallback for prompt %s", task_id, prompt_id)
        deadline = asyncio.get_event_loop().time() + timeout
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
        from api.services.ffmpeg_utils import extract_last_frame, extract_first_frame, concat_videos
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

        # Story mode: track the initial reference image filename (identity anchor)
        # Check if segments already have initial_ref_filename set (from single segment generation)
        initial_ref_filename = segments[0].get("initial_ref_filename", "") if segments else ""

        try:
            await self.redis.hset(f"chain:{chain_id}", "status", "running")

            # ── Merged story mode: single ComfyUI prompt with shared models ──
            # Only use merged story workflow if first_frame mode (has image_filename AND image_mode is first_frame)
            # For face_reference mode (only face_image_filename), use standard T2V workflow
            seg0_image_mode = segments[0].get("image_mode", "first_frame")
            if story_mode and segments[0].get("image_filename") and seg0_image_mode == "first_frame":
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
                client = self.clients.get(model.value)
                if not client:
                    raise RuntimeError(f"ComfyUI {model.value} not available")

                # ── Story mode branch (fallback: no start image) ──
                if story_mode:
                    workflow = await self._build_story_segment(
                        i, seg, segments, video_paths, client,
                        chain_id, segment_prompts, original_prompt,
                        total, optimizer, auto_continue, initial_ref_filename,
                    )
                    mode = GenerateMode.I2V

                # ── Standard chain mode ──
                # For segments after the first, use VLM to generate continuation prompt
                elif i > 0 and video_paths and auto_continue:
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
                    workflow = build_workflow(
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
                    workflow = build_workflow(
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

                    workflow = build_workflow(
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

                task_id = await self.create_task(mode, model, workflow, params=seg, chain_id=chain_id)
                task_ids.append(task_id)
                await self.redis.hset(f"chain:{chain_id}", "segment_task_ids", json.dumps(task_ids))

                # Wait for this segment to complete
                video_path = await self._wait_for_task_completion(task_id, timeout=1800)
                if not video_path:
                    raise RuntimeError(f"Segment {i+1}/{total} (task {task_id}) failed")

                video_paths.append(video_path)
                segment_filenames.append(video_path.name)

                # Story mode: after first segment, establish the initial reference image
                if story_mode and i == 0 and not initial_ref_filename:
                    if seg.get("image_filename"):
                        # Had a start image — use it as identity anchor
                        initial_ref_filename = seg["image_filename"]
                    else:
                        # T2V start — extract first frame as identity anchor
                        ref_frame_path = await extract_first_frame(video_path)
                        ref_frame_data = ref_frame_path.read_bytes()
                        ref_upload = await client.upload_image(ref_frame_data, ref_frame_path.name)
                        initial_ref_filename = ref_upload.get("name", ref_frame_path.name)
                    logger.info("Chain %s: story initial_ref_filename=%s", chain_id, initial_ref_filename)

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

        except Exception as e:
            logger.exception("Chain %s failed at segment %d: %s", chain_id,
                             int(await self.redis.hget(f"chain:{chain_id}", "current_segment") or 0), e)
            completed = int(await self.redis.hget(f"chain:{chain_id}", "completed_segments") or 0)
            status = "partial" if completed > 0 else "failed"
            await self.redis.hset(f"chain:{chain_id}", mapping={
                "status": status,
                "error": str(e),
                "completed_at": str(int(time.time())),
                "segment_task_ids": json.dumps(task_ids),
            })

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
        client = self.clients.get(model.value)
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

        if image_mode == "face_reference" and total == 1:
            # Single segment face_reference mode: use T2V workflow
            logger.info("Chain %s: single segment face_reference mode, using T2V workflow", chain_id)
            from api.models.enums import GenerateMode
            from api.models.schemas import FaceSwapConfig

            # Prepare face_swap_config
            face_swap_cfg = seg0.get("face_swap")
            if face_swap_cfg and isinstance(face_swap_cfg, dict):
                face_swap_cfg = FaceSwapConfig(**face_swap_cfg)

            workflow = build_workflow(
                mode=GenerateMode.T2V,
                model=model,
                prompt=seg0["prompt"],
                negative_prompt=seg0.get("negative_prompt", ""),
                width=seg0["width"], height=seg0["height"],
                num_frames=seg0["num_frames"], fps=seg0.get("fps", 16),
                steps=seg0.get("steps", 20), cfg=seg0.get("cfg", 6.0), shift=seg0.get("shift", 5.0),
                seed=seg0.get("seed"), loras=seg0.get("loras", []),
                scheduler=seg0.get("scheduler", "unipc"),
                model_preset=seg0.get("model_preset", ""),
                upscale=seg0.get("upscale", False),
                t5_preset=seg0.get("t5_preset", ""),
                face_swap_config=face_swap_cfg,
                face_image_path=face_image_filename or image_filename,
            )

            # Create task and wait for completion
            task_id = await self.create_task(
                GenerateMode.T2V, model, workflow,
                params={"face_reference_t2v": True},
                chain_id=chain_id,
            )
            task_ids = [task_id]
            await self.redis.hset(f"chain:{chain_id}", mapping={
                "segment_task_ids": json.dumps(task_ids),
                "current_segment": "0",
            })

            video_path = await self._wait_for_task_completion(task_id, timeout=1800)
            if not video_path:
                raise RuntimeError(f"T2V face_reference workflow (task {task_id}) failed")

            task_data = await self.redis.hgetall(f"task:{task_id}")
            video_url = task_data.get("video_url", "")

            await self.redis.hset(f"chain:{chain_id}", mapping={
                "status": "completed",
                "completed_at": str(int(asyncio.get_event_loop().time())),
                "final_video_url": video_url,
            })
            return

        logger.info("Chain %s: building merged story workflow for %d segments", chain_id, total)

        # Extract face_image_filename from segment 0 if present
        face_image_filename = seg0.get("face_image_filename", "")
        face_swap_strength = seg0.get("face_swap_strength", 1.0)
        detect_gender_source = seg0.get("detect_gender_source", "no")
        detect_gender_input = seg0.get("detect_gender_input", "no")

        # Build single merged workflow
        workflow = build_merged_story_workflow(
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
            upscale_model=seg0.get("upscale_model", "4x-UltraSharp"),
            upscale_resize=seg0.get("upscale_resize", "2x"),
            enable_interpolation=seg0.get("enable_interpolation", False),
            interpolation_multiplier=seg0.get("interpolation_multiplier", 2),
            interpolation_profile=seg0.get("interpolation_profile", "small"),
            enable_mmaudio=seg0.get("enable_mmaudio", False),
            mmaudio_prompt=seg0.get("mmaudio_prompt", ""),
            mmaudio_negative_prompt=seg0.get("mmaudio_negative_prompt", ""),
            mmaudio_steps=seg0.get("mmaudio_steps", 25),
            mmaudio_cfg=seg0.get("mmaudio_cfg", 4.5),
            face_image_filename=face_image_filename,
            face_swap_strength=face_swap_strength,
            detect_gender_source=detect_gender_source,
            detect_gender_input=detect_gender_input,
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
        if not video_path:
            raise RuntimeError(f"Merged story workflow (task {task_id}) failed")

        # With ImageBatchMulti optimization, merged workflow now outputs only 1 video
        # (all segments merged in ComfyUI, no need for external ffmpeg concat)
        task_data = await self.redis.hgetall(f"task:{task_id}")
        prompt_id = task_data.get("prompt_id", "")
        if not prompt_id:
            raise RuntimeError("No prompt_id found for merged story task")

        output_files = await client.get_output_files_ordered(prompt_id)
        if not output_files:
            # Post-processing nodes may have failed (e.g. RIFE TRT), but the task
            # itself may have completed with a video from the preview node.
            # Fall back to the task's already-saved video_url if available.
            task_video_url = task_data.get("video_url", "")
            if task_video_url:
                logger.warning("Chain %s: no output files from final node, using task video_url fallback", chain_id)
                await self.redis.hset(f"chain:{chain_id}", mapping={
                    "status": "completed",
                    "completed_segments": str(total),
                    "final_video_url": task_video_url,
                    "completed_at": str(int(time.time())),
                    "segment_task_ids": json.dumps(task_ids),
                })
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

        await self.redis.hset(f"chain:{chain_id}", mapping={
            "status": "completed",
            "final_video_url": final_url,
            "completed_at": str(int(time.time())),
            "segment_task_ids": json.dumps(task_ids),
        })
        logger.info("Chain %s (merged story) completed: %s", chain_id, final_url)

    async def _build_story_segment(
        self, i: int, seg: dict, segments: list[dict],
        video_paths: list, client, chain_id: str,
        segment_prompts: list, original_prompt: str,
        total: int, optimizer, auto_continue: bool,
        initial_ref_filename: str,
    ) -> dict:
        """Build a story workflow for segment i (PainterI2V or PainterLongVideo)."""
        from api.services.ffmpeg_utils import extract_last_frame, extract_last_n_frames_video
        import base64

        seg["segment_index"] = i
        seg["target_prompt"] = segment_prompts[i] if i < len(segment_prompts) else seg["prompt"]
        seg["story_mode"] = True

        prev_video_filename = ""

        # VLM prompt continuation for seg2+ (same as standard chain)
        if i > 0 and video_paths and auto_continue:
            frame_path = await extract_last_frame(video_paths[-1])
            frame_data = frame_path.read_bytes()
            frame_b64 = base64.b64encode(frame_data).decode()

            prev_prompt = segments[i - 1]["prompt"]
            target_prompt = seg["target_prompt"]
            new_prompt = await optimizer.continue_prompt(
                original_prompt=original_prompt,
                frame_image_base64=frame_b64,
                segment_index=i,
                total_segments=total,
                target_prompt=target_prompt,
                previous_prompt=prev_prompt,
            )
            seg["prompt"] = new_prompt
            seg["vlm_prompt"] = new_prompt
            seg["final_prompt"] = new_prompt
            logger.info("Chain %s seg %d (story): VLM prompt: %s", chain_id, i, new_prompt[:100])

            # Extract last N frames as short video for motion reference
            motion_frames = seg.get("motion_frames", 5)
            fps = seg.get("fps", 16)
            short_video_path = await extract_last_n_frames_video(video_paths[-1], motion_frames, fps)
            video_data = short_video_path.read_bytes()
            upload_result = await client.upload_video(video_data, short_video_path.name)
            prev_video_filename = upload_result.get("name", short_video_path.name)
        elif i > 0 and video_paths:
            # No VLM but still need previous video for story continuation
            seg["final_prompt"] = seg["prompt"]
            motion_frames = seg.get("motion_frames", 5)
            fps = seg.get("fps", 16)
            short_video_path = await extract_last_n_frames_video(video_paths[-1], motion_frames, fps)
            video_data = short_video_path.read_bytes()
            upload_result = await client.upload_video(video_data, short_video_path.name)
            prev_video_filename = upload_result.get("name", short_video_path.name)
        else:
            seg["final_prompt"] = seg["prompt"]

        # Check if this segment has a parent video (continuation from previous chain)
        parent_video_fn = seg.get("parent_video_filename", "")

        if i == 0 and parent_video_fn:
            # First segment but continuing from parent video: use PainterLongVideo
            logger.info("Chain %s seg 0: continuation from parent video %s", chain_id, parent_video_fn)
            workflow = build_story_workflow(
                is_first_segment=False,
                prompt=seg["prompt"],
                negative_prompt=seg.get("negative_prompt", ""),
                width=seg["width"], height=seg["height"],
                num_frames=seg["num_frames"],
                seed=seg.get("seed"),
                shift=seg.get("shift", 8.0),
                cfg=seg.get("cfg", 1.0),
                steps=seg.get("steps", 20),
                motion_amplitude=seg.get("motion_amplitude", 1.15),
                motion_frames=seg.get("motion_frames", 5),
                boundary=seg.get("boundary", 0.9),
                video_filename=parent_video_fn,
                initial_ref_filename=initial_ref_filename,
                model_preset=seg.get("model_preset", "nsfw_v2"),
                clip_preset=seg.get("clip_preset", "nsfw"),
                fps=seg.get("fps", 16),
                upscale=seg.get("upscale", False),
                loras=seg.get("loras", []),
            )
        elif i == 0:
            # First segment: check image mode
            image_filename = seg.get("image_filename", "")
            face_image_filename = seg.get("face_image_filename", "")
            image_mode = seg.get("image_mode", "first_frame")

            # If face_reference mode, use T2V workflow
            if image_mode == "face_reference":
                logger.info("Chain %s: seg 0 face_reference mode, using T2V workflow", chain_id)
                from api.models.enums import GenerateMode
                from api.models.schemas import FaceSwapConfig

                # Prepare face_swap_config
                face_swap_cfg = seg.get("face_swap")
                if face_swap_cfg and isinstance(face_swap_cfg, dict):
                    face_swap_cfg = FaceSwapConfig(**face_swap_cfg)

                workflow = build_workflow(
                    mode=GenerateMode.T2V,
                    model=ModelType(seg["model"]),
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
                    face_swap_config=face_swap_cfg,
                    face_image_path=face_image_filename or image_filename,
                )
                return workflow

            if not image_filename:
                # No start image — fall back to standard T2V for first segment
                logger.info("Chain %s: story mode seg 0 has no image, using T2V fallback", chain_id)
                from api.models.enums import GenerateMode
                workflow = build_workflow(
                    mode=GenerateMode.T2V,
                    model=ModelType(seg["model"]),
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
                )
                return workflow

            # First_frame mode: use PainterI2V
            workflow = build_story_workflow(
                is_first_segment=True,
                prompt=seg["prompt"],
                negative_prompt=seg.get("negative_prompt", ""),
                width=seg["width"], height=seg["height"],
                num_frames=seg["num_frames"],
                seed=seg.get("seed"),
                shift=seg.get("shift", 8.0),
                cfg=seg.get("cfg", 1.0),
                steps=seg.get("steps", 20),
                motion_amplitude=seg.get("motion_amplitude", 1.15),
                motion_frames=seg.get("motion_frames", 5),
                boundary=seg.get("boundary", 0.9),
                image_filename=image_filename,
                model_preset=seg.get("model_preset", "nsfw_v2"),
                clip_preset=seg.get("clip_preset", "nsfw"),
                fps=seg.get("fps", 16),
                upscale=seg.get("upscale", False),
                loras=seg.get("loras", []),
            )
        else:
            # Continuation segment: PainterLongVideo with full video reference
            workflow = build_story_workflow(
                is_first_segment=False,
                prompt=seg["prompt"],
                negative_prompt=seg.get("negative_prompt", ""),
                width=seg["width"], height=seg["height"],
                num_frames=seg["num_frames"],
                seed=seg.get("seed"),
                shift=seg.get("shift", 8.0),
                cfg=seg.get("cfg", 1.0),
                steps=seg.get("steps", 20),
                motion_amplitude=seg.get("motion_amplitude", 1.15),
                motion_frames=seg.get("motion_frames", 5),
                boundary=seg.get("boundary", 0.9),
                video_filename=prev_video_filename,
                initial_ref_filename=initial_ref_filename,
                model_preset=seg.get("model_preset", "nsfw_v2"),
                clip_preset=seg.get("clip_preset", "nsfw"),
                fps=seg.get("fps", 16),
                upscale=seg.get("upscale", False),
                loras=seg.get("loras", []),
            )

        # Inject post-processing nodes (upscale, RIFE, MMAudio) into single-segment story workflow
        if seg.get("enable_upscale") or seg.get("enable_interpolation") or seg.get("enable_mmaudio"):
            workflow = _inject_story_postproc(workflow, seg)

        return workflow

    async def _wait_for_task_completion(self, task_id: str, timeout: float = 1800) -> Optional['Path']:
        """Poll Redis until task completes. Returns local video path or None."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            data = await self.redis.hgetall(f"task:{task_id}")
            if not data:
                return None
            status = data.get("status")
            if status == TaskStatus.COMPLETED.value:
                video_url = data.get("video_url", "")
                return await storage.get_video_path_from_url(video_url)
            if status == TaskStatus.FAILED.value:
                return None
            await asyncio.sleep(3)
        return None

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
