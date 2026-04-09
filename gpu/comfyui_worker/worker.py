"""GPU Worker -- polls Redis for tasks and executes ComfyUI workflows."""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from typing import TYPE_CHECKING

import aiohttp

from shared.enums import TaskStatus
from shared.redis_keys import queue_key, task_key

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from gpu.comfyui_worker.comfyui_client import ComfyUIClient
    from gpu.comfyui_worker.config import WorkerConfig
    from gpu.comfyui_worker.heartbeat import HeartbeatReporter
    from gpu.comfyui_worker.instance_pool import InstancePool
    from shared.cos import COSClient
    from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)

MAX_OOM_RETRIES = 2
OOM_COOLDOWN = 5


def _is_connection_error(error: Exception) -> bool:
    """Check if error is a transient connection failure (not OOM, not logic)."""
    if _is_oom_error(error):
        return False
    conn_types = (
        ConnectionError, ConnectionRefusedError, ConnectionResetError,
        TimeoutError, OSError,
    )
    if isinstance(error, conn_types):
        return True
    if isinstance(error, aiohttp.ClientError):
        return True
    msg = str(error).lower()
    return any(s in msg for s in (
        "connect call failed", "cannot connect", "connection refused",
        "server disconnected", "ssl:", "timed out",
        "not reachable", "unreachable",
    ))


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
        self._pool: "InstancePool | None" = None
        self._health_checker = None

    @property
    def model_keys(self) -> list[str]:
        return self._config.model_keys

    def _get_client(self, model_key: str) -> "ComfyUIClient":
        """Get or create a ComfyUI client for the given model key.

        Uses InstancePool for dynamic instance selection if available,
        otherwise falls back to static config (backward compat).
        """
        from gpu.comfyui_worker.comfyui_client import ComfyUIClient

        # Dynamic pool: pick a healthy instance
        if self._pool is not None:
            url = self._pool.get_instance(model_key)
            if url:
                # Cache client per URL (not per model_key — URLs can change)
                if url not in self._comfyui_clients:
                    self._comfyui_clients[url] = ComfyUIClient(url)
                return self._comfyui_clients[url]

        # Static fallback
        if model_key in self._comfyui_clients:
            return self._comfyui_clients[model_key]

        url = self._config.comfyui_urls.get(model_key)
        if not url:
            raise RuntimeError(
                f"No ComfyUI URL configured for model key '{model_key}'"
            )
        client = ComfyUIClient(url)
        self._comfyui_clients[model_key] = client
        return client

    async def run(self) -> None:
        """Main loop: BLPOP from model queues, process, repeat."""
        queue_names = [queue_key(mk) for mk in self.model_keys]
        if not queue_names:
            logger.warning("No model keys configured -- worker has nothing to poll")
            return

        # Initialize instance pool and health checker
        from gpu.comfyui_worker.instance_pool import InstancePool
        from gpu.comfyui_worker.health_checker import HealthChecker

        self._pool = InstancePool(
            static_urls=self._config.comfyui_urls,
            redis=self._redis,
            failure_threshold=self._config.instance_failure_threshold,
            cooldown_base=self._config.instance_cooldown_base,
            cooldown_max=self._config.instance_cooldown_max,
        )
        # Seed from Redis registry at startup
        await self._pool.refresh_from_registry()

        self._health_checker = HealthChecker(
            pool=self._pool,
            interval=self._config.health_check_interval,
        )
        self._health_checker.start()

        # Attach pool to heartbeat for health reporting
        if self._heartbeat is not None:
            self._heartbeat.set_instance_pool(self._pool)

        logger.info(
            "Worker %s started, polling queues: %s",
            self._config.worker_id,
            queue_names,
        )

        try:
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
                    # Connection errors: retry on a different instance
                    if _is_connection_error(exc) and self._pool is not None:
                        await self._handle_connection_retry(task_id, exc)
                    else:
                        logger.exception("Task %s failed: %s", task_id, exc)
                        await self._gateway.mark_task_failed(task_id, error=str(exc))
                finally:
                    if self._heartbeat is not None:
                        self._heartbeat.set_status("idle")
        finally:
            if self._health_checker:
                await self._health_checker.close()

        logger.info("Worker %s stopped", self._config.worker_id)

    async def _handle_connection_retry(self, task_id: str, exc: Exception) -> None:
        """Re-enqueue a task that failed due to connection error."""
        raw = await self._redis.hgetall(task_key(task_id))
        retry_count = int(raw.get("_retry_count", 0))
        model = raw.get("model", "a14b")
        max_retries = self._config.task_connection_retries

        # Report failure to pool so the instance gets cooldown
        comfyui_url = raw.get("comfyui_url", "")
        if comfyui_url and self._pool:
            self._pool.report_failure(model, comfyui_url)

        if retry_count < max_retries:
            retry_count += 1
            await self._redis.hset(task_key(task_id), mapping={
                "_retry_count": str(retry_count),
                "status": TaskStatus.QUEUED.value,
                "error": "",
            })
            # LPUSH to front of queue — task already waited its turn
            await self._redis.lpush(queue_key(model), task_id)
            logger.warning(
                "Task %s connection error, re-enqueued (retry %d/%d): %s",
                task_id, retry_count, max_retries, exc,
            )
        else:
            logger.error(
                "Task %s connection error, retries exhausted (%d/%d): %s",
                task_id, retry_count, max_retries, exc,
            )
            await self._gateway.mark_task_failed(
                task_id,
                error=f"All ComfyUI instances unreachable after {max_retries} retries: {exc}",
            )

    async def _process_task(self, task_id: str) -> None:
        """Dispatch to the appropriate handler based on task mode."""
        from shared.enums import GenerateMode

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

        raw_data = await self._redis.hgetall(task_key(task_id))
        mode = raw_data.get("mode", GenerateMode.T2V.value)

        postprocess_modes = {
            GenerateMode.INTERPOLATE.value,
            GenerateMode.UPSCALE.value,
            GenerateMode.AUDIO.value,
            GenerateMode.FACESWAP.value,
        }
        if mode == GenerateMode.CONCAT.value:
            await self._process_concat_task(task_id, raw_data)
        elif mode == GenerateMode.LORA_DOWNLOAD.value:
            await self._process_lora_download_task(task_id, raw_data)
        elif mode in postprocess_modes:
            # FACESWAP with a workflow field is an image face swap via ComfyUI,
            # not video postprocess (which expects source_video in params)
            if raw_data.get("workflow") and mode == GenerateMode.FACESWAP.value:
                await self._process_comfyui_task(task_id, raw_data)
            else:
                await self._process_postprocess_task(task_id, raw_data)
        else:
            await self._process_comfyui_task(task_id, raw_data)

    async def _process_comfyui_task(self, task_id: str, raw_data: dict) -> None:
        """Process a ComfyUI workflow task:

        1. Read workflow + input_files from raw_data
        2. Mark as running
        3. Download input files from COS -> upload to ComfyUI (if any)
        4. Submit workflow to ComfyUI
        5. Wait for completion (WebSocket + polling fallback)
        6. Download result video from ComfyUI
        7. Upload video to COS
        8. Optional: extract last frame -> upload to COS
        9. Mark as completed with video_url
        """
        workflow = json.loads(raw_data["workflow"])
        model_key = raw_data.get("model", "a14b")
        client = self._get_client(model_key)

        # Pre-flight: verify ComfyUI is reachable before accepting the task
        if not await client.is_alive():
            raise RuntimeError(
                f"ComfyUI at {client.base_url} is not reachable for model '{model_key}'. "
                f"The instance may be down or still starting up."
            )

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

            # Step 4: Wait for ComfyUI to be free before submitting
            wait_count = 0
            while True:
                running = await client.get_running_prompt_id()
                if running is None:
                    break
                if wait_count == 0:
                    logger.info("Task %s: ComfyUI busy (prompt %s running), waiting...",
                                task_id, running[:12])
                wait_count += 1
                if wait_count > 360:  # 30 min max wait
                    raise RuntimeError(f"ComfyUI busy for >30min, aborting task {task_id}")
                await asyncio.sleep(5)
            if wait_count > 0:
                logger.info("Task %s: ComfyUI free after %ds wait", task_id, wait_count * 5)

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

            # Step 5: Wait for completion with real-time progress
            async def _on_step_progress(value, max_val):
                if max_val > 0:
                    # Map ComfyUI step progress (0..max) to task progress (0.05..0.50)
                    # Leave 0.50-1.0 for VAE decode + COS upload (smooth fill in frontend)
                    step_pct = value / max_val
                    task_pct = 0.05 + 0.45 * step_pct
                    await self._gateway.update_task_progress(task_id, round(task_pct, 3))

            await client.wait_for_completion(prompt_id, timeout=1800, on_progress=_on_step_progress)
            await self._gateway.update_task_progress(task_id, 0.9)
            logger.info("Task %s ComfyUI execution completed", task_id)

            # Step 6: Download result from ComfyUI
            output_files = await client.get_output_files(prompt_id)
            # Fallback to images for non-video workflows (e.g. face swap)
            if not output_files:
                output_files = await client.get_output_images(prompt_id)
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
            lossless_last_frame_url = ""
            if raw_data.get("extract_last_frame") == "1":
                last_frame_url = await self._extract_and_upload_last_frame(
                    task_id, video_data, ext
                )
                # Lossless variant: in-workflow SaveImage saved a raw VAE-decoded
                # PNG (filename prefix ``wan22_story_lastframe``). Pull it from
                # ComfyUI history and upload to COS for downstream continuations.
                lossless_last_frame_url = await self._extract_and_upload_lossless_last_frame(
                    task_id, prompt_id, client
                )

            # Step 9: Mark as completed
            await self._gateway.mark_task_completed(
                task_id,
                video_url=video_url,
                last_frame_url=last_frame_url,
                lossless_last_frame_url=lossless_last_frame_url,
            )
            # Report success to instance pool
            if self._pool is not None:
                self._pool.report_success(model_key, client.base_url)
            logger.info("Task %s completed with video_url=%s", task_id, video_url)

        except Exception as exc:
            # OOM retry logic
            if _is_oom_error(exc):
                await self._handle_oom(task_id, model_key, client)
            raise

    async def _process_concat_task(self, task_id: str, raw_data: dict) -> None:
        """Concatenate multiple video segments using ffmpeg.

        Expects raw_data["workflow"] to be a JSON object with:
          {"video_urls": ["https://...", "https://..."], "output_filename": "chain_xyz.mp4"}

        Downloads each segment from COS (or public URL), runs ffmpeg concat,
        uploads result to COS, marks task completed.
        """
        await self._gateway.mark_task_running(task_id, comfyui_url="local-ffmpeg")
        logger.info("Task %s (concat) started", task_id)

        try:
            params = json.loads(raw_data.get("workflow", "{}"))
            video_urls: list[str] = params.get("video_urls", [])
            output_filename: str = params.get("output_filename", f"{task_id}_concat.mp4")

            if not video_urls:
                raise RuntimeError("concat task has no video_urls")

            await self._gateway.update_task_progress(task_id, 0.05)

            # Download each segment to a temp file
            tmp_dir = tempfile.mkdtemp(prefix="concat_")
            segment_paths: list[str] = []
            try:
                for i, url in enumerate(video_urls):
                    cos_key = self._cos_client.parse_cos_url(url)
                    if cos_key:
                        data = self._download_from_cos(cos_key)
                    else:
                        # Fallback: download via HTTP
                        import aiohttp as _aiohttp
                        async with _aiohttp.ClientSession() as sess:
                            async with sess.get(url) as resp:
                                if resp.status != 200:
                                    raise RuntimeError(
                                        f"Failed to download segment {i}: HTTP {resp.status}"
                                    )
                                data = await resp.read()
                    seg_path = os.path.join(tmp_dir, f"seg_{i:04d}.mp4")
                    with open(seg_path, "wb") as f:
                        f.write(data)
                    segment_paths.append(seg_path)
                    progress = 0.05 + 0.60 * (i + 1) / len(video_urls)
                    await self._gateway.update_task_progress(task_id, progress)

                # Build ffmpeg concat list file
                list_path = os.path.join(tmp_dir, "concat_list.txt")
                with open(list_path, "w") as f:
                    for p in segment_paths:
                        f.write(f"file '{p}'\n")

                output_path = os.path.join(tmp_dir, output_filename)
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", list_path,
                    "-c", "copy",
                    output_path,
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        f"ffmpeg concat failed: {result.stderr[-500:]}"
                    )

                await self._gateway.update_task_progress(task_id, 0.90)

                # Upload result to COS
                with open(output_path, "rb") as f:
                    video_data = f.read()

                video_url = await self._upload_to_cos(
                    video_data, "videos", output_filename
                )
                await self._gateway.update_task_progress(task_id, 0.95)

            finally:
                # Clean up temp files
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

            await self._gateway.mark_task_completed(task_id, video_url=video_url)
            logger.info("Task %s (concat) completed: %s", task_id, video_url)

        except Exception as exc:
            logger.exception("Concat task %s failed: %s", task_id, exc)
            await self._gateway.mark_task_failed(task_id, error=str(exc))
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
            # Prefix with task_id to ensure unique filenames per task,
            # preventing ComfyUI from caching LoadImage node outputs.
            basename = f"{task_id[:8]}_{os.path.basename(cos_key)}"

            # Download from COS to temp file
            file_data = self._download_from_cos(cos_key)

            # Dispatch image vs video upload by extension. Both go to
            # ComfyUI's /upload/image endpoint, but the video helper sets
            # the correct Content-Type so VHS_LoadVideo can read it back.
            ext = os.path.splitext(basename)[1].lower()
            if ext in (".mp4", ".webm", ".mov", ".mkv"):
                result = await client.upload_video(file_data, basename)
            else:
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

    async def _extract_and_upload_lossless_last_frame(
        self,
        task_id: str,
        prompt_id: str,
        client: "ComfyUIClient",
    ) -> str:
        """Pull the in-workflow lossless PNG from ComfyUI and upload to COS.

        The shared workflow_builder injects a ``SaveImage`` node with
        ``filename_prefix=wan22_story_lastframe`` that captures the raw
        VAE-decoded last frame BEFORE VHS_VideoCombine re-encodes the
        video. This bypasses h264 compression artifacts entirely, giving
        downstream continuations a higher-quality identity anchor.

        Returns an empty string on any failure (the field is optional —
        callers fall back to the regular ``last_frame_url``).
        """
        try:
            output_images = await client.get_output_images(prompt_id)
        except Exception as exc:
            logger.warning(
                "Task %s: lossless frame query failed: %s", task_id, exc,
            )
            return ""

        # Find the SaveImage output produced by _inject_lossless_frame_save.
        lossless_files = [
            f for f in output_images
            if f.get("filename", "").startswith("wan22_story_lastframe")
        ]
        if not lossless_files:
            logger.debug(
                "Task %s: no wan22_story_lastframe output found "
                "(workflow may not have the SaveImage injection)", task_id,
            )
            return ""

        # If multiple matches (rare), pick the most recent one — ComfyUI
        # numbers them sequentially, so the lex-max is correct.
        chosen = max(lossless_files, key=lambda f: f.get("filename", ""))

        try:
            png_data = await client.download_file(
                chosen["filename"],
                chosen.get("subfolder", ""),
                chosen.get("type", "output"),
            )
        except Exception as exc:
            logger.warning(
                "Task %s: lossless frame download failed: %s", task_id, exc,
            )
            return ""

        if not png_data:
            return ""

        try:
            url = await self._upload_to_cos(
                png_data, "frames", f"{task_id}_lossless_last.png",
            )
            logger.info(
                "Task %s lossless last frame uploaded: %s",
                task_id, url,
            )
            return url
        except Exception as exc:
            logger.warning(
                "Task %s: lossless frame COS upload failed: %s", task_id, exc,
            )
            return ""

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

    async def _process_postprocess_task(self, task_id: str, raw_data: dict) -> None:
        """Handle INTERPOLATE / UPSCALE / AUDIO / FACESWAP tasks.

        Workflow dict (set by API gateway) must contain ``source_video`` (COS URL).
        The worker downloads the video, uploads to ComfyUI, builds the workflow,
        submits to ComfyUI, downloads result, uploads to COS, marks completed.
        """
        import aiohttp as _aiohttp
        from shared.enums import GenerateMode

        mode = raw_data.get("mode", "")
        model_key = raw_data.get("model", "a14b")
        client = self._get_client(model_key)

        if not await client.is_alive():
            raise RuntimeError(
                f"ComfyUI at {client.base_url} is not reachable for model '{model_key}'. "
                f"The instance may be down or still starting up."
            )

        await self._gateway.mark_task_running(task_id, comfyui_url=client.base_url)
        logger.info("Task %s (%s) started", task_id, mode)

        try:
            params = json.loads(raw_data.get("workflow", "{}"))
            source_video_url: str = params.get("source_video", "")
            if not source_video_url:
                raise RuntimeError("postprocess task missing source_video in workflow")

            await self._gateway.update_task_progress(task_id, 0.05)

            # Download source video
            cos_key = self._cos_client.parse_cos_url(source_video_url)
            if cos_key:
                video_data = self._download_from_cos(cos_key)
            else:
                async with _aiohttp.ClientSession() as sess:
                    async with sess.get(source_video_url) as resp:
                        if resp.status != 200:
                            raise RuntimeError(
                                f"Failed to download source video: HTTP {resp.status}"
                            )
                        video_data = await resp.read()

            await self._gateway.update_task_progress(task_id, 0.15)

            # Write to temp file and upload to ComfyUI
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vtmp:
                vtmp.write(video_data)
                video_tmp_path = vtmp.name
            try:
                video_filename = await client.upload_video(video_tmp_path)
            finally:
                if os.path.exists(video_tmp_path):
                    os.unlink(video_tmp_path)

            await self._gateway.update_task_progress(task_id, 0.25)

            # Build mode-specific workflow
            from shared.workflow_builder import (
                build_interpolate_workflow,
                build_upscale_workflow,
                build_audio_workflow,
                build_face_swap_workflow,
            )

            if mode == GenerateMode.INTERPOLATE.value:
                workflow = build_interpolate_workflow(
                    video_path=video_filename,
                    multiplier=int(params.get("multiplier", 2)),
                    fps=float(params.get("fps", 16.0)),
                )
            elif mode == GenerateMode.UPSCALE.value:
                workflow = build_upscale_workflow(
                    video_path=video_filename,
                    upscale_model=params.get("model", "4x_foolhardy_Remacri"),
                    resize_to=params.get("resize_to", "2x"),
                )
            elif mode == GenerateMode.AUDIO.value:
                workflow = build_audio_workflow(
                    video_path=video_filename,
                    prompt=params.get("prompt", ""),
                    negative_prompt=params.get("negative_prompt", ""),
                    steps=int(params.get("steps", 25)),
                    cfg=float(params.get("cfg", 4.5)),
                    fps=float(params.get("fps", 16.0)),
                )
            elif mode == GenerateMode.FACESWAP.value:
                face_url = params.get("face_image", "")
                if not face_url:
                    raise RuntimeError("faceswap task missing face_image in workflow")
                face_cos_key = self._cos_client.parse_cos_url(face_url)
                if face_cos_key:
                    face_data = self._download_from_cos(face_cos_key)
                else:
                    async with _aiohttp.ClientSession() as sess:
                        async with sess.get(face_url) as resp:
                            face_data = await resp.read()
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as ftmp:
                    ftmp.write(face_data)
                    face_tmp_path = ftmp.name
                try:
                    face_filename = await client.upload_image(face_tmp_path)
                finally:
                    if os.path.exists(face_tmp_path):
                        os.unlink(face_tmp_path)
                workflow = build_face_swap_workflow(
                    frame_filename=video_filename,
                    face_filename=face_filename,
                    strength=float(params.get("strength", 1.0)),
                )
            else:
                raise RuntimeError(f"Unhandled postprocess mode: {mode}")

            await self._gateway.update_task_progress(task_id, 0.35)

            # Submit to ComfyUI and wait
            prompt_id = await client.queue_prompt(workflow)
            output_data = await client.wait_for_output(prompt_id, task_id=task_id)

            await self._gateway.update_task_progress(task_id, 0.85)

            # Upload result to COS
            output_filename = f"{task_id}_{mode}.mp4"
            video_url = await self._upload_to_cos(output_data, "videos", output_filename)

            await self._gateway.mark_task_completed(task_id, video_url=video_url)
            if self._pool is not None:
                self._pool.report_success(model_key, client.base_url)
            logger.info("Task %s (%s) completed: %s", task_id, mode, video_url)

        except Exception as exc:
            if _is_oom_error(exc):
                await self._handle_oom(task_id, model_key, self._get_client(model_key))
            logger.exception("Postprocess task %s (%s) failed: %s", task_id, mode, exc)
            await self._gateway.mark_task_failed(task_id, error=str(exc))
            raise

    async def _process_lora_download_task(self, task_id: str, raw_data: dict) -> None:
        """Download a LoRA from CivitAI and republish the lora list."""
        await self._gateway.mark_task_running(task_id, comfyui_url="local-download")
        logger.info("Task %s (lora_download) started", task_id)

        try:
            params = json.loads(raw_data.get("workflow", "{}"))
            version_id = params.get("civitai_version_id")
            filename = params.get("filename", "")

            if not version_id or not filename:
                raise RuntimeError("lora_download task missing civitai_version_id or filename")

            loras_dir = self._config.loras_dir
            if not loras_dir:
                raise RuntimeError("LORAS_DIR not configured on this worker")

            token = self._config.civitai_api_token
            url = f"https://civitai.com/api/download/models/{version_id}"
            if token:
                url += f"?token={token}"

            dest_path = os.path.join(loras_dir, filename)
            await self._gateway.update_task_progress(task_id, 0.05)

            result = subprocess.run(
                ["curl", "-L", "-o", dest_path, url],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                raise RuntimeError(f"curl download failed: {result.stderr[-500:]}")

            # Verify file size > 1 MB
            size = os.path.getsize(dest_path)
            if size < 1024 * 1024:
                os.unlink(dest_path)
                raise RuntimeError(
                    f"Downloaded file too small ({size} bytes) — likely not a valid LoRA"
                )

            await self._gateway.update_task_progress(task_id, 0.90)

            # Republish LoRA list
            if self._heartbeat is not None:
                await self._heartbeat._publish_loras()

            await self._gateway.mark_task_completed(task_id, video_url="")
            logger.info("Task %s (lora_download) completed: %s", task_id, filename)

        except Exception as exc:
            logger.exception("LoRA download task %s failed: %s", task_id, exc)
            await self._gateway.mark_task_failed(task_id, error=str(exc))
            raise

    async def close(self) -> None:
        """Close all ComfyUI client sessions."""
        for client in self._comfyui_clients.values():
            await client.close()
        self._comfyui_clients.clear()

    async def stop(self) -> None:
        """Signal the worker loop to stop."""
        self._running = False
        logger.info("Worker %s stop requested", self._config.worker_id)
