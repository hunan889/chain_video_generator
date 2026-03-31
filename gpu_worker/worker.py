"""GPU Worker -- polls Redis for tasks and executes ComfyUI workflows."""

import json
import logging
import os
import subprocess
import tempfile
from typing import TYPE_CHECKING

from shared.enums import TaskStatus
from shared.redis_keys import queue_key, task_key

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from gpu_worker.comfyui_client import ComfyUIClient
    from gpu_worker.config import WorkerConfig
    from gpu_worker.heartbeat import HeartbeatReporter
    from shared.cos import COSClient
    from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)

MAX_OOM_RETRIES = 2
OOM_COOLDOWN = 5


def _is_oom_error(error: Exception) -> bool:
    """Check if error is a CUDA OOM error."""
    msg = str(error).lower()
    return "cuda out of memory" in msg or "out of memory" in msg


class GPUWorker:
    """Polls Redis for tasks and executes ComfyUI workflows."""

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
        self._comfyui_clients: dict[str, "ComfyUIClient"] = {}

    @property
    def model_keys(self) -> list[str]:
        return self._config.model_keys

    def _get_client(self, model_key: str) -> "ComfyUIClient":
        """Get or create a ComfyUI client for the given model key."""
        if model_key in self._comfyui_clients:
            return self._comfyui_clients[model_key]

        url = self._config.comfyui_urls.get(model_key)
        if not url:
            raise RuntimeError(
                f"No ComfyUI URL configured for model key '{model_key}'"
            )

        from gpu_worker.comfyui_client import ComfyUIClient

        client = ComfyUIClient(url)
        self._comfyui_clients[model_key] = client
        return client

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
        """Process a single task:

        1. Read task data from Redis (workflow JSON + input_files)
        2. Mark as running
        3. Download input files from COS -> upload to ComfyUI (if any)
        4. Submit workflow to ComfyUI
        5. Wait for completion (WebSocket + polling fallback)
        6. Download result video from ComfyUI
        7. Upload video to COS
        8. Optional: extract last frame -> upload to COS
        9. Mark as completed with video_url
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

        # Read raw task data for fields not exposed by get_task
        raw_data = await self._redis.hgetall(task_key(task_id))
        workflow = json.loads(raw_data["workflow"])
        model_key = raw_data.get("model", "a14b")
        client = self._get_client(model_key)

        # Step 2: Mark as running
        await self._gateway.mark_task_running(
            task_id, comfyui_url=client.base_url
        )
        logger.info("Task %s marked as running on %s", task_id, client.base_url)

        try:
            # Step 3: Handle input files (if any)
            workflow = await self._handle_input_files(
                task_id, workflow, raw_data, client
            )

            # Step 4: Submit workflow to ComfyUI
            prompt_id = await client.queue_prompt(workflow)
            await self._redis.hset(
                task_key(task_id),
                mapping={"prompt_id": prompt_id, "progress": "0.05"},
            )
            logger.info(
                "Task %s submitted to ComfyUI, prompt_id=%s",
                task_id,
                prompt_id,
            )

            # Step 5: Wait for completion
            await client.wait_for_completion(prompt_id, timeout=1800)
            await self._gateway.update_task_progress(task_id, 0.9)
            logger.info("Task %s ComfyUI execution completed", task_id)

            # Step 6: Download result from ComfyUI
            output_files = await client.get_output_files(prompt_id)
            if not output_files:
                raise RuntimeError(
                    f"No output files generated for task {task_id}"
                )

            first_file = output_files[0]
            video_data = await client.download_file(
                first_file["filename"],
                first_file.get("subfolder", ""),
                first_file.get("type", "output"),
            )

            # Step 7: Upload video to COS
            ext = (
                first_file["filename"].rsplit(".", 1)[-1]
                if "." in first_file["filename"]
                else "mp4"
            )
            video_url = await self._upload_to_cos(
                video_data, "videos", f"{task_id}.{ext}"
            )
            logger.info("Task %s video uploaded: %s", task_id, video_url)

            # Step 8: Optional last frame extraction
            last_frame_url = ""
            if raw_data.get("extract_last_frame") == "1":
                last_frame_url = await self._extract_and_upload_last_frame(
                    task_id, video_data, ext
                )

            # Step 9: Mark as completed
            await self._gateway.mark_task_completed(
                task_id,
                video_url=video_url,
                last_frame_url=last_frame_url,
            )
            logger.info("Task %s completed with video_url=%s", task_id, video_url)

        except Exception as exc:
            # OOM retry logic
            if _is_oom_error(exc):
                await self._handle_oom(task_id, model_key, client)
            raise

    async def _handle_input_files(
        self,
        task_id: str,
        workflow: dict,
        raw_data: dict,
        client: "ComfyUIClient",
    ) -> dict:
        """Download input files from COS and upload them to ComfyUI.

        Replaces placeholder strings in the workflow with actual filenames
        returned by ComfyUI after upload. Returns the updated workflow.
        """
        input_files_json = raw_data.get("input_files", "[]")
        input_files = json.loads(input_files_json)
        if not input_files:
            return workflow

        for file_info in input_files:
            cos_key = file_info["cos_key"]
            placeholder = file_info["placeholder"]
            basename = os.path.basename(cos_key)

            # Download from COS to temp file
            file_data = self._download_from_cos(cos_key)

            # Upload to ComfyUI
            result = await client.upload_image(file_data, basename)
            actual_name = result.get("name", basename)

            # Replace placeholder in workflow
            workflow_str = json.dumps(workflow)
            workflow_str = workflow_str.replace(placeholder, actual_name)
            workflow = json.loads(workflow_str)

            logger.info(
                "Task %s: replaced placeholder '%s' with '%s'",
                task_id,
                placeholder,
                actual_name,
            )

        return workflow

    def _download_from_cos(self, cos_key: str) -> bytes:
        """Download file bytes from COS using a temporary file.

        The cos_key format is 'subdir/filename'.
        """
        parts = cos_key.rsplit("/", 1)
        if len(parts) == 2:
            subdir, filename = parts
        else:
            subdir, filename = "", parts[0]

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            self._cos_client.download_file(subdir, filename, tmp_path)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def _upload_to_cos(
        self, data: bytes, subdir: str, filename: str
    ) -> str:
        """Write bytes to a temp file and upload to COS. Returns public URL."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            return self._cos_client.upload_file(tmp_path, subdir, filename)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def _extract_and_upload_last_frame(
        self, task_id: str, video_data: bytes, video_ext: str
    ) -> str:
        """Extract the last frame from video data using ffmpeg and upload to COS."""
        video_tmp = None
        frame_tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=f".{video_ext}", delete=False
            ) as vf:
                vf.write(video_data)
                video_tmp = vf.name

            with tempfile.NamedTemporaryFile(
                suffix=".png", delete=False
            ) as ff:
                frame_tmp = ff.name

            result = subprocess.run(
                [
                    "ffmpeg",
                    "-sseof",
                    "-0.1",
                    "-i",
                    video_tmp,
                    "-frames:v",
                    "1",
                    "-y",
                    frame_tmp,
                ],
                capture_output=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.warning(
                    "ffmpeg last-frame extraction failed for task %s: %s",
                    task_id,
                    result.stderr.decode(errors="replace"),
                )
                return ""

            if not os.path.exists(frame_tmp) or os.path.getsize(frame_tmp) == 0:
                logger.warning(
                    "ffmpeg produced empty frame for task %s", task_id
                )
                return ""

            return self._cos_client.upload_file(
                frame_tmp, "frames", f"{task_id}_last.png"
            )
        finally:
            for path in (video_tmp, frame_tmp):
                if path and os.path.exists(path):
                    os.unlink(path)

    async def _handle_oom(
        self,
        task_id: str,
        model_key: str,
        client: "ComfyUIClient",
    ) -> None:
        """Handle CUDA OOM: free memory, optionally re-queue the task."""
        logger.warning("Task %s hit CUDA OOM, freeing memory", task_id)
        await client.free_memory()

        # Re-read retry_count from Redis (may have been updated externally)
        current_data = await self._redis.hgetall(task_key(task_id))
        retry_count = int(current_data.get("retry_count", "0"))
        if retry_count < MAX_OOM_RETRIES:
            new_count = retry_count + 1
            await self._redis.hset(
                task_key(task_id),
                mapping={
                    "retry_count": str(new_count),
                    "status": TaskStatus.QUEUED.value,
                },
            )
            await self._redis.rpush(queue_key(model_key), task_id)
            logger.info(
                "Task %s re-queued (retry %d/%d)",
                task_id,
                new_count,
                MAX_OOM_RETRIES,
            )
        else:
            logger.error(
                "Task %s OOM retries exhausted (%d/%d)",
                task_id,
                retry_count,
                MAX_OOM_RETRIES,
            )

    async def close(self) -> None:
        """Close all ComfyUI client sessions."""
        for client in self._comfyui_clients.values():
            await client.close()
        self._comfyui_clients.clear()

    async def stop(self) -> None:
        """Signal the worker loop to stop."""
        self._running = False
        logger.info("Worker %s stop requested", self._config.worker_id)
