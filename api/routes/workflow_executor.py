"""
Advanced workflow execution logic.

This module contains the async orchestration logic for the advanced workflow system.
"""
import asyncio
import logging
import base64
import json
import time
from typing import Optional, Any
import aiohttp
from api.config import UPLOADS_DIR

logger = logging.getLogger(__name__)


def _detect_face_in_image_bytes(image_bytes: bytes) -> bool:
    """Detect if an image contains a human face using OpenCV Haar cascade.

    Returns True if at least one face is detected.
    """
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return False
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        return len(faces) > 0
    except Exception as e:
        logger.warning("Face detection failed: %s", e)
        return False


# Global registry of active workflow tasks for graceful shutdown
_active_workflow_tasks: set[asyncio.Task] = set()


async def wait_for_active_workflows(timeout: float = 120):
    """Wait for all active workflow tasks to complete (called on shutdown)."""
    if not _active_workflow_tasks:
        return
    logger.info("Shutdown: waiting for %d active workflow task(s) to finish (timeout=%ds)...",
                len(_active_workflow_tasks), timeout)
    done, pending = await asyncio.wait(_active_workflow_tasks, timeout=timeout)
    if pending:
        logger.warning("Shutdown: %d workflow task(s) still running after timeout, cancelling...", len(pending))
        for t in pending:
            t.cancel()
    else:
        logger.info("Shutdown: all workflow tasks finished gracefully")


def _get_config(req, stage: str, key: str, default: Any = None) -> Any:
    """
    Get configuration value with priority: internal_config > legacy params > default

    Args:
        req: WorkflowGenerateRequest object
        stage: Stage name (e.g., "stage1_prompt_analysis", "stage2_first_frame")
        key: Configuration key
        default: Default value if not found

    Returns:
        Configuration value
    """
    result = None
    source = None

    # Priority 1: internal_config
    if req.internal_config and stage in req.internal_config:
        stage_config = req.internal_config[stage]
        if key in stage_config:
            result = stage_config[key]
            source = "internal_config"
            logger.debug(f"[CONFIG] {stage}.{key} = {result} (from {source})")
            return result

    # Priority 2: Legacy parameters
    if stage == "stage1_prompt_analysis":
        if key == "auto_analyze":
            result = req.auto_analyze
            source = "legacy"
        elif key == "auto_lora":
            result = req.auto_lora
            source = "legacy"
        elif key == "auto_prompt":
            result = req.auto_prompt
            source = "legacy"
        elif key == "auto_completion":
            result = getattr(req, 'auto_completion', None)
            source = "legacy"

    elif stage == "stage2_first_frame":
        if key == "first_frame_source":
            # Priority 1: internal_config already checked above
            # Priority 2: Legacy parameter
            if hasattr(req.first_frame_source, 'value'):
                result = req.first_frame_source.value
            else:
                result = req.first_frame_source
            source = "legacy"
        elif key == "t2i" and req.t2i_params:
            result = req.t2i_params
            source = "legacy"

    elif stage == "stage3_seedream":
        if req.seedream_params:
            if key == "mode":
                result = req.seedream_params.get("edit_mode", default)
                source = "legacy"
            elif key == "enable_reactor":
                result = req.seedream_params.get("enable_reactor_first", default)
                source = "legacy"
            elif key in req.seedream_params:
                result = req.seedream_params[key]
                source = "legacy"

    elif stage == "stage4_video":
        # Check nested generation config first
        if key in ["model", "resolution", "duration", "steps", "cfg", "shift", "scheduler", "noise_aug_strength", "motion_amplitude", "model_preset"]:
            # These are under stage4_video.generation in internal_config
            if req.internal_config and "stage4_video" in req.internal_config:
                generation_config = req.internal_config["stage4_video"].get("generation", {})
                if key in generation_config:
                    result = generation_config[key]
                    source = "internal_config.generation"
                    logger.debug(f"[CONFIG] {stage}.generation.{key} = {result} (from {source})")
                    return result
        # Fallback to legacy video_params
        if req.video_params and key in req.video_params:
            result = req.video_params[key]
            source = "legacy"

    # Priority 3: Default value
    if result is None:
        result = default
        source = "default"

    logger.debug(f"[CONFIG] {stage}.{key} = {result} (from {source})")
    return result


def get_default_seedream_prompt(mode: str = None, *, swap_face: bool = True, swap_accessories: bool = True, swap_expression: bool = False, swap_clothing: bool = False) -> str:
    """
    Get default SeeDream prompt based on toggle switches or legacy mode.

    Args:
        mode: Legacy edit mode (face_wearings, full_body). If provided, maps to toggles.
        swap_face: Swap face identity from reference
        swap_accessories: Swap accessories from reference
        swap_expression: Swap facial expression from reference
        swap_clothing: Swap clothing from reference

    Returns:
        Default prompt string
    """
    # If legacy mode is provided and no explicit toggles were set, map to toggles
    if mode and mode != "custom":
        if mode == "face_only":
            swap_face, swap_accessories, swap_expression, swap_clothing = True, False, False, False
        elif mode == "face_wearings":
            swap_face, swap_accessories, swap_expression, swap_clothing = True, True, False, False
        elif mode == "full_body":
            swap_face, swap_accessories, swap_expression, swap_clothing = True, True, False, True

    from api.routes.workflow import _build_seedream_prompt
    return _build_seedream_prompt(swap_face, swap_accessories, swap_expression, swap_clothing)


async def _apply_face_swap_to_frame(
    frame_url: str,
    reference_face: str,
    strength: float = 1.0,
    task_manager = None
) -> Optional[str]:
    """
    Apply face swap to a single frame using Reactor.

    Args:
        frame_url: URL of the frame image (can be relative path like /api/v1/results/...)
        reference_face: Reference face image (base64 or URL)
        strength: Face swap strength (0.0-1.0)
        task_manager: TaskManager instance

    Returns:
        URL of the face-swapped image, or None if failed
    """
    try:
        import requests as http_requests
        from api.config import FORGE_URL, API_HOST, API_PORT
        from api.services import storage
        import uuid

        # Normalize frame_url to full URL if it's a relative path
        if frame_url.startswith('/') and not frame_url.startswith('//'):
            frame_url = f"http://{API_HOST}:{API_PORT}{frame_url}"
            logger.info(f"Normalized relative frame URL to: {frame_url}")

        # Download frame image
        async with aiohttp.ClientSession() as session:
            async with session.get(frame_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to download frame: {resp.status}")
                    return None
                frame_data = await resp.read()

        frame_b64 = base64.b64encode(frame_data).decode()

        # Decode reference face
        if reference_face.startswith('data:image'):
            face_b64 = reference_face.split(',')[1]
        elif reference_face.startswith('http://') or reference_face.startswith('https://'):
            async with aiohttp.ClientSession() as session:
                async with session.get(reference_face, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.error(f"Failed to download reference face: {resp.status}")
                        return None
                    face_data = await resp.read()
            face_b64 = base64.b64encode(face_data).decode()
        elif reference_face.startswith('/uploads/'):
            # Path like "/uploads/filename.jpg"
            filename = reference_face.split('/')[-1]
            local_path = UPLOADS_DIR / filename
            if local_path.exists():
                face_data = local_path.read_bytes()
                face_b64 = base64.b64encode(face_data).decode()
            else:
                logger.error(f"Local file not found: {reference_face}")
                return None
        elif '/' not in reference_face and '.' in reference_face:
            # Local filename (e.g., "abc123.png")
            local_path = UPLOADS_DIR / reference_face
            if local_path.exists():
                face_data = local_path.read_bytes()
                face_b64 = base64.b64encode(face_data).decode()
            else:
                logger.error(f"Local file not found: {reference_face}")
                return None
        else:
            # Assume it's already base64
            face_b64 = reference_face

        # Call Reactor API
        reactor_payload = {
            "source_image": face_b64,
            "target_image": frame_b64,
            "source_faces_index": [0],
            "face_index": [0],
            "model": "inswapper_128.onnx",
            "face_restorer": "CodeFormer",
            "restorer_visibility": 0.85,
            "codeformer_weight": 0.3,
            "restore_first": 1,
            "facedetection": "retinaface_resnet50",
            "upscaler": "None",
            "scale": 1,
            "upscale_visibility": 1,
            "device": "CUDA",
            "mask_face": 1,
            "det_thresh": 0.5,
            "det_maxnum": 0,
        }

        logger.info(f"Applying face swap to frame with strength={strength}")
        from api.routes.image import _async_post
        reactor_resp = await _async_post(
            f"{FORGE_URL}/reactor/image", json=reactor_payload, timeout=120
        )

        if reactor_resp.status_code == 200:
            resp_json = reactor_resp.json()
            swapped_b64 = resp_json["image"]
            swapped_data = base64.b64decode(swapped_b64)

            # Log Reactor response info for debugging
            info_keys = [k for k in resp_json.keys() if k != "image"]
            if info_keys:
                logger.info(f"Reactor response info: {info_keys}")
                for key in info_keys:
                    logger.info(f"  {key}: {resp_json[key]}")

            # Save result
            filename = f"face_swap_{uuid.uuid4().hex[:8]}.png"
            local_path, url = await storage.save_upload(swapped_data, filename)

            logger.info(f"Face swap completed: {url}")
            return url
        else:
            logger.error(f"Reactor failed: {reactor_resp.status_code}, response: {reactor_resp.text[:200]}")
            return None

    except Exception as e:
        logger.error(f"Face swap failed: {e}", exc_info=True)
        return None


async def _apply_face_swap_via_comfyui(
    frame_url: str,
    reference_face: str,
    strength: float = 1.0,
    task_manager=None,
    workflow_id: str = None,
) -> Optional[str]:
    """Apply face swap using ComfyUI ReActor (bypasses Forge, uses idle worker).

    Same contract as _apply_face_swap_to_frame: returns URL on success, None on failure.
    """
    try:
        from api.services import storage
        from api.services.workflow_builder import build_face_swap_workflow
        from api.config import API_HOST, API_PORT
        import uuid

        # ── 1. Download frame image ──────────────────────────────────────
        if frame_url.startswith('/') and not frame_url.startswith('//'):
            frame_url = f"http://{API_HOST}:{API_PORT}{frame_url}"

        async with aiohttp.ClientSession() as session:
            async with session.get(frame_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.error("ComfyUI face swap: failed to download frame: %s", resp.status)
                    return None
                frame_data = await resp.read()

        # ── 2. Decode reference face ─────────────────────────────────────
        if reference_face.startswith('data:image'):
            face_data = base64.b64decode(reference_face.split(',')[1])
        elif reference_face.startswith('http://') or reference_face.startswith('https://'):
            async with aiohttp.ClientSession() as session:
                async with session.get(reference_face, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.error("ComfyUI face swap: failed to download reference face: %s", resp.status)
                        return None
                    face_data = await resp.read()
        elif reference_face.startswith('/uploads/'):
            filename = reference_face.split('/')[-1]
            local_path = UPLOADS_DIR / filename
            if not local_path.exists():
                logger.error("ComfyUI face swap: local file not found: %s", reference_face)
                return None
            face_data = local_path.read_bytes()
        elif '/' not in reference_face and '.' in reference_face:
            local_path = UPLOADS_DIR / reference_face
            if not local_path.exists():
                logger.error("ComfyUI face swap: local file not found: %s", reference_face)
                return None
            face_data = local_path.read_bytes()
        else:
            face_data = base64.b64decode(reference_face)

        # ── 3. Find idle ComfyUI worker ──────────────────────────────────
        if not task_manager:
            logger.error("ComfyUI face swap: no task_manager provided")
            return None

        client = await task_manager.find_available_client(timeout=120)
        if not client:
            logger.error("ComfyUI face swap: no idle ComfyUI worker available (timeout)")
            return None

        try:
            logger.info("ComfyUI face swap: using worker %s", client.base_url)

            # ── 4. Upload both images to worker ──────────────────────────────
            frame_name = f"fs_frame_{uuid.uuid4().hex[:8]}.png"
            face_name = f"fs_face_{uuid.uuid4().hex[:8]}.png"
            await client.upload_image(frame_data, frame_name)
            await client.upload_image(face_data, face_name)

            # ── 5. Build & submit workflow ───────────────────────────────────
            workflow = build_face_swap_workflow(frame_name, face_name, strength)
            prompt_id = await client.queue_prompt(workflow)
            logger.info("ComfyUI face swap: submitted prompt %s", prompt_id)

            # ── 6. Wait for completion (polling only, no WebSocket) ──────────
            # WebSocket breaks during graceful reload, so use pure polling.
            # Also update workflow heartbeat to prevent orphan false positive.
            deadline = asyncio.get_event_loop().time() + 120
            while asyncio.get_event_loop().time() < deadline:
                history = await client.get_history(prompt_id)
                if history and history.get("status", {}).get("completed", False):
                    break
                if history and history.get("outputs"):
                    break
                # Update heartbeat to prevent orphan false positive during face swap
                if task_manager and workflow_id:
                    await task_manager.redis.hset(
                        f"workflow:{workflow_id}",
                        "executor_heartbeat", str(int(time.time()))
                    )
                await asyncio.sleep(2)
            else:
                logger.error("ComfyUI face swap: timeout waiting for prompt %s", prompt_id)
                return None

            # ── 7. Get result image ──────────────────────────────────────────
            images = await client.get_output_images(prompt_id)
            if not images:
                logger.error("ComfyUI face swap: no output images for prompt %s", prompt_id)
                return None

            result_file = images[0]
            result_data = await client.download_file(
                result_file["filename"],
                subfolder=result_file.get("subfolder", ""),
                file_type=result_file.get("type", "output"),
            )

            # ── 8. Save & return URL ─────────────────────────────────────────
            out_filename = f"face_swap_{uuid.uuid4().hex[:8]}.png"
            _local_path, url = await storage.save_upload(result_data, out_filename)
            logger.info("ComfyUI face swap completed: %s", url)
            return url
        finally:
            task_manager.release_client(client)

    except Exception as e:
        logger.error("ComfyUI face swap failed: %s", e, exc_info=True)
        return None


async def _execute_workflow(workflow_id: str, req, task_manager, resume: bool = False):
    """
    Execute the complete advanced workflow asynchronously.

    This function orchestrates:
    1. Prompt analysis and LORA recommendation
    2. First frame acquisition (upload/generate/select)
    3. SeeDream editing
    4. Video generation via Chain workflow

    When resume=True, already-completed stages are skipped and their
    results are restored from Redis, allowing workflows to continue
    after a service restart.
    """
    try:
        # =============================================
        # Resume support: check which stages completed
        # =============================================
        _resumed_stages = {}
        if resume:
            wf_data = await task_manager.redis.hgetall(f"workflow:{workflow_id}")
            for sn in ["prompt_analysis", "first_frame_acquisition", "seedream_edit", "video_generation"]:
                if wf_data.get(f"stage_{sn}") == "completed":
                    _resumed_stages[sn] = True
            # Reset status back to running
            await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
                "status": "running",
                "error": "",
                "completed_at": "",
            })
            logger.info(f"[{workflow_id}] Resuming workflow, completed stages: {list(_resumed_stages.keys())}")

        # =============================================
        # Story Continuation: load parent workflow data
        # =============================================
        is_continuation = bool(getattr(req, 'parent_workflow_id', None))
        parent_workflow = None
        origin_first_frame_url = None

        if is_continuation:
            parent_workflow = await task_manager.redis.hgetall(f"workflow:{req.parent_workflow_id}")
            if not parent_workflow:
                raise Exception(f"Parent workflow {req.parent_workflow_id} not found")
            if parent_workflow.get("status") != "completed":
                raise Exception(f"Parent workflow is not completed (status: {parent_workflow.get('status')})")
            if not parent_workflow.get("final_video_url"):
                raise Exception("Parent workflow has no video output")

            logger.info(f"[{workflow_id}] Story continuation from parent: {req.parent_workflow_id}")
            logger.info(f"[{workflow_id}] Parent video: {parent_workflow.get('final_video_url')}")

            # Resolve origin first frame for CLIP Vision identity anchoring
            # This prevents face drift across multiple continuations (A→B→C)
            # Fast path: parent already has origin_first_frame_url cached
            origin_first_frame_url = parent_workflow.get("origin_first_frame_url")
            visited = set()
            if not origin_first_frame_url:
                # Slow path: trace back through the workflow chain to find the root's first frame
                trace_wf_id = req.parent_workflow_id
                visited = set()
                while trace_wf_id and trace_wf_id not in visited:
                    visited.add(trace_wf_id)
                    trace_wf = await task_manager.redis.hgetall(f"workflow:{trace_wf_id}")
                    if not trace_wf:
                        break
                    # Check if this workflow has origin cached
                    cached_origin = trace_wf.get("origin_first_frame_url")
                    if cached_origin:
                        origin_first_frame_url = cached_origin
                        break
                    # Check if this workflow has a parent (is itself a continuation)
                    trace_parent_id = None
                    try:
                        trace_stages = json.loads(trace_wf.get("stages", "[]")) if isinstance(trace_wf.get("stages"), str) else []
                        for s in (trace_stages if isinstance(trace_stages, list) else []):
                            if isinstance(s, dict) and s.get("name") == "prompt_analysis":
                                sub = json.loads(s.get("sub_stage", "{}")) if isinstance(s.get("sub_stage"), str) else s.get("sub_stage", {})
                                trace_parent_id = sub.get("parent_workflow_id") if isinstance(sub, dict) else None
                                break
                    except (json.JSONDecodeError, TypeError):
                        pass
                    if trace_parent_id:
                        trace_wf_id = trace_parent_id
                    else:
                        # This is the root workflow — grab its first_frame_url
                        # For T2V roots (no input image), fallback to lossless last frame
                        origin_first_frame_url = (
                            trace_wf.get("first_frame_url")
                            or trace_wf.get("lossless_last_frame_url")
                        )
                        break
            # continuation_index = how many generations deep (1 = first continue, 2 = second, etc.)
            continuation_index = len(visited)  # visited contains all ancestor workflow IDs
            if origin_first_frame_url:
                logger.info(f"[{workflow_id}] Origin first frame for CLIP Vision anchor: {origin_first_frame_url} (depth={continuation_index})")
            else:
                # Fast path was used, count from parent's cached depth
                continuation_index = int(parent_workflow.get("continuation_index", "0")) + 1
                logger.info(f"[{workflow_id}] No origin first frame found (root may be T2V), depth={continuation_index}")

            # Auto-inherit reference_image from parent if not provided
            if not req.reference_image and parent_workflow.get("reference_image"):
                req.reference_image = parent_workflow["reference_image"]
                logger.info(f"[{workflow_id}] Inherited reference_image from parent")

            # Fallback: use root's first_frame_url as reference_image for face swap
            if not req.reference_image and origin_first_frame_url:
                req.reference_image = origin_first_frame_url
                logger.info(f"[{workflow_id}] Using origin first_frame_url as reference_image: {origin_first_frame_url}")

            # Inherit internal_config from parent if not provided by user
            if not req.internal_config:
                parent_config_str = parent_workflow.get("internal_config", "{}")
                try:
                    req.internal_config = json.loads(parent_config_str)
                    logger.info(f"[{workflow_id}] Inherited internal_config from parent")
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"[{workflow_id}] Failed to parse parent internal_config, using defaults")

        # Validate configuration based on mode (skip for continuation - parent already validated)
        if not is_continuation and req.mode in ["face_reference", "full_body_reference"]:
            # Check if reference_image is provided
            if not req.reference_image:
                raise Exception(f"{req.mode} mode requires reference_image parameter")

            # Check if first frame is from user upload
            is_user_upload = (req.mode == "first_frame" or
                            (hasattr(req, 'uploaded_first_frame') and req.uploaded_first_frame))

            if not is_user_upload:
                stage2_face_swap = _get_config(req, "stage2_first_frame", "face_swap", {})
                stage3_enabled = _get_config(req, "stage3_seedream", "enabled", True)

                reactor_enabled = stage2_face_swap.get("enabled", False) if isinstance(stage2_face_swap, dict) else False

                if req.mode == "face_reference":
                    # face_reference: 至少启用一个 (Reactor 或 SeeDream)
                    if not reactor_enabled and not stage3_enabled:
                        raise Exception("face_reference mode requires at least one of: stage2 face_swap or stage3 seedream")

                elif req.mode == "full_body_reference":
                    # full_body_reference: SeeDream 必选
                    if not stage3_enabled:
                        raise Exception("full_body_reference mode requires stage3 seedream to be enabled")

        # Stage 1: Prompt Analysis
        analysis_result = None
        auto_analyze = _get_config(req, "stage1_prompt_analysis", "auto_analyze", True)

        if not _resumed_stages.get("prompt_analysis"):
            await _update_stage(task_manager, workflow_id, "prompt_analysis", "running")

        if _resumed_stages.get("prompt_analysis"):
            # Resume: restore analysis_result from Redis
            ar_raw = await task_manager.redis.hget(f"workflow:{workflow_id}", "analysis_result")
            if ar_raw:
                try:
                    analysis_result = json.loads(ar_raw)
                except (json.JSONDecodeError, TypeError):
                    pass
            logger.info(f"[{workflow_id}] Stage 1 skipped (already completed), analysis_result={'yes' if analysis_result else 'no'}")
        elif is_continuation:
            # Story continuation: generate continuation prompt based on parent's video prompt
            auto_completion = int(_get_config(req, "stage1_prompt_analysis", "auto_completion", 2))
            logger.info(f"[{workflow_id}] Continuation auto_completion level: {auto_completion}")
            if auto_analyze:
                # Step 1: Get parent's video generation prompt (the actual prompt sent to ComfyUI)
                parent_video_prompt = ""
                try:
                    # Primary: read from stage_video_generation_details hash field
                    vg_details_raw = parent_workflow.get("stage_video_generation_details", "")
                    if vg_details_raw:
                        vg_details = json.loads(vg_details_raw) if isinstance(vg_details_raw, str) else vg_details_raw
                        parent_video_prompt = vg_details.get("prompt", "")
                    # Fallback: try legacy stages array
                    if not parent_video_prompt:
                        for stage in json.loads(parent_workflow.get("stages", "[]")):
                            if stage.get("name") == "video_generation":
                                sub = json.loads(stage.get("sub_stage", "{}")) if isinstance(stage.get("sub_stage"), str) else stage.get("sub_stage", {})
                                parent_video_prompt = sub.get("prompt", "") if isinstance(sub, dict) else ""
                                break
                except (json.JSONDecodeError, TypeError):
                    pass
                logger.info(f"[{workflow_id}] Parent video prompt extracted ({len(parent_video_prompt)} chars): {parent_video_prompt[:150]}")

                # Step 2: Get parent's last frame as base64 for VLM analysis
                # Prefer lossless PNG to avoid H.264 artifacts in VLM input
                parent_last_frame_b64 = ""
                try:
                    lossless_url = parent_workflow.get("lossless_last_frame_url")
                    if lossless_url:
                        from api.config import UPLOADS_DIR
                        # Lossless frames are saved via save_upload → UPLOADS_DIR
                        filename = lossless_url.rsplit("/", 1)[-1]
                        frame_path = UPLOADS_DIR / filename
                        if frame_path.exists():
                            parent_last_frame_b64 = base64.b64encode(frame_path.read_bytes()).decode()
                            logger.info(f"[{workflow_id}] Using lossless frame for VLM analysis")
                    if not parent_last_frame_b64:
                        parent_video_url = parent_workflow.get("final_video_url")
                        if parent_video_url:
                            from api.services import storage
                            from api.services.ffmpeg_utils import extract_last_frame
                            video_path = await storage.get_video_path_from_url(parent_video_url)
                            if video_path and video_path.exists():
                                last_frame_path = await extract_last_frame(video_path)
                                parent_last_frame_b64 = base64.b64encode(last_frame_path.read_bytes()).decode()
                except Exception as e:
                    logger.warning(f"[{workflow_id}] Failed to extract parent last frame for VLM: {e}")

                # Step 2b: Detect if parent's last frame contains a face
                parent_last_frame_has_face = False
                if parent_last_frame_b64:
                    parent_last_frame_has_face = _detect_face_in_image_bytes(base64.b64decode(parent_last_frame_b64))
                    logger.info(f"[{workflow_id}] Parent last frame face detection: {'FACE FOUND' if parent_last_frame_has_face else 'NO FACE'}")

                # Resolve effective user_intent: explicit user input > parent's actual video prompt
                effective_user_intent = req.user_prompt.strip() or parent_video_prompt
                logger.info(f"[{workflow_id}] Continuation user_intent ({len(effective_user_intent)} chars, explicit={bool(req.user_prompt.strip())}): {effective_user_intent[:150]}")

                # Step 3: Generate continuation prompt using LLM
                # auto_completion >= 1: use LLM to generate continuation prompt
                # auto_completion == 0: skip LLM, use raw user_prompt
                continuation_prompt = ""
                if auto_completion >= 1 and parent_video_prompt:
                    try:
                        from api.services.prompt_optimizer import PromptOptimizer
                        optimizer = PromptOptimizer()
                        cont_duration = float(str(_get_config(req, "stage4_video", "duration", "3s")).rstrip('s'))
                        continuation_prompt = await optimizer.generate_continuation_prompt(
                            user_intent=effective_user_intent,
                            previous_video_prompt=parent_video_prompt,
                            frame_image_base64=parent_last_frame_b64,
                            duration=cont_duration,
                            continuation_index=continuation_index,
                        )
                        logger.info(f"[{workflow_id}] Continuation prompt generated: {continuation_prompt[:150]}")
                    except Exception as e:
                        logger.warning(f"[{workflow_id}] continue_prompt failed: {e}, falling back to parent prompt")
                elif auto_completion < 1:
                    logger.info(f"[{workflow_id}] auto_completion=0: skipping LLM continuation prompt, using raw user_prompt")

                # Step 4: Use continuation prompt as user_prompt for LoRA selection
                if auto_completion < 1:
                    # Level 0: use raw user_prompt directly, skip LLM optimization
                    raw_prompt = req.user_prompt.strip() or parent_video_prompt or "continue the scene"
                    analysis_result = await _analyze_prompt(req, task_manager)
                    if analysis_result and "_error" not in analysis_result:
                        analysis_result["video_prompt"] = raw_prompt
                        analysis_result["optimized_i2v_prompt"] = raw_prompt
                        analysis_result["optimized_prompt"] = raw_prompt
                        logger.info(f"[{workflow_id}] auto_completion=0: video_prompt = raw user_prompt ({len(raw_prompt)} chars)")
                elif continuation_prompt:
                    # Temporarily override user_prompt for analysis, then restore
                    original_user_prompt = req.user_prompt
                    req.user_prompt = continuation_prompt
                    analysis_result = await _analyze_prompt(req, task_manager)
                    req.user_prompt = original_user_prompt  # restore
                    # Override the video_prompt in analysis_result with our continuation prompt
                    if analysis_result and "_error" not in analysis_result:
                        analysis_result["video_prompt"] = continuation_prompt
                        analysis_result["optimized_i2v_prompt"] = continuation_prompt
                        analysis_result["optimized_prompt"] = continuation_prompt
                else:
                    # Fallback: use parent video prompt as-is (better than re-generating from user prompt)
                    # Ensure req.user_prompt is non-empty for _analyze_prompt
                    original_user_prompt = req.user_prompt
                    if not req.user_prompt.strip():
                        req.user_prompt = effective_user_intent or "continue the scene"
                    analysis_result = await _analyze_prompt(req, task_manager)
                    req.user_prompt = original_user_prompt  # restore
                    if analysis_result and "_error" not in analysis_result and parent_video_prompt:
                        analysis_result["video_prompt"] = parent_video_prompt
                        analysis_result["optimized_i2v_prompt"] = parent_video_prompt
                        analysis_result["optimized_prompt"] = parent_video_prompt
                        logger.info(f"[{workflow_id}] Continuation fallback: using parent video prompt")

                if analysis_result and "_error" not in analysis_result:
                    await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
                        "analysis_result": json.dumps(analysis_result)
                    })
                    details_dict = {
                        "video_loras": analysis_result.get('video_loras', []),
                        "image_loras": analysis_result.get('image_loras', []),
                        "images": analysis_result.get('images', []),
                        "pose_keys": analysis_result.get('pose_keys', []),
                        "original_prompt": req.user_prompt,
                        "optimized_prompt": analysis_result.get('optimized_i2v_prompt') or analysis_result.get('optimized_t2i_prompt'),
                        "continuation": True,
                        "parent_workflow_id": req.parent_workflow_id,
                        "parent_video_prompt": parent_video_prompt[:200] if parent_video_prompt else "",
                    }
                    await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details_dict=details_dict)
                else:
                    error_msg = analysis_result.get("_error", "未知错误") if analysis_result else "返回空结果"
                    logger.warning(f"[{workflow_id}] Continuation prompt analysis failed: {error_msg}")
                    await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details_dict={
                        "error": f"分析失败: {error_msg}",
                        "original_prompt": req.user_prompt,
                        "continuation": True,
                    })
            else:
                await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details_dict={
                    "skipped": True, "reason": "未启用", "continuation": True,
                })
            logger.info(f"[{workflow_id}] Continuation Stage 1: continuation prompt analysis")

        elif auto_analyze:
            analysis_result = await _analyze_prompt(req, task_manager)
            if analysis_result and "_error" not in analysis_result:
                await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
                    "analysis_result": json.dumps(analysis_result)
                })

                # 保存结构化详细信息
                details_dict = {
                    "video_loras": analysis_result.get('video_loras', []),
                    "image_loras": analysis_result.get('image_loras', []),
                    "images": analysis_result.get('images', []),
                    "pose_keys": analysis_result.get('pose_keys', []),
                    "original_prompt": req.user_prompt,
                    "optimized_prompt": analysis_result.get('optimized_i2v_prompt') or analysis_result.get('optimized_t2i_prompt')
                }

                await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details_dict=details_dict)
            else:
                error_msg = analysis_result.get("_error", "未知错误") if analysis_result else "返回空结果"
                logger.warning(f"[{workflow_id}] Prompt analysis failed: {error_msg}")
                await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details_dict={
                    "error": f"分析失败: {error_msg}",
                    "original_prompt": req.user_prompt
                })
        else:
            await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details_dict={"skipped": True, "reason": "未启用"})

        # Stage 2: First Frame Acquisition
        await task_manager.redis.hset(f"workflow:{workflow_id}", "current_stage", "first_frame_acquisition")

        if _resumed_stages.get("first_frame_acquisition"):
            # Resume: restore first_frame_url from Redis
            first_frame_url = await task_manager.redis.hget(f"workflow:{workflow_id}", "first_frame_url") or None
            logger.info(f"[{workflow_id}] Stage 2 skipped (already completed), first_frame_url={first_frame_url}")
        elif is_continuation:
            # Extract last frame from parent for continuation start frame
            # Prefer pre-saved lossless PNG (avoids cumulative H.264 degradation)
            await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "running", details_dict={
                "source": "parent_video_last_frame",
                "parent_workflow_id": req.parent_workflow_id,
            })

            from api.services import storage

            lossless_frame_url = parent_workflow.get("lossless_last_frame_url")
            if lossless_frame_url:
                first_frame_url = lossless_frame_url
                frame_source = "lossless_png"
                logger.info(f"[{workflow_id}] Using lossless last frame from parent: {lossless_frame_url}")
            else:
                # Fallback: extract from H.264 video (legacy workflows without lossless frame)
                parent_video_url = parent_workflow.get("final_video_url")
                if not parent_video_url:
                    raise Exception("Parent workflow has no final video for continuation")

                from api.services.ffmpeg_utils import extract_last_frame

                video_path = await storage.get_video_path_from_url(parent_video_url)
                if not video_path or not video_path.exists():
                    raise Exception(f"Parent video file not found: {parent_video_url}")

                last_frame_path = await extract_last_frame(video_path)
                frame_data = last_frame_path.read_bytes()
                _, first_frame_url = await storage.save_upload(frame_data, last_frame_path.name)
                frame_source = "h264_extraction"
                logger.info(f"[{workflow_id}] Fallback: extracted last frame from H.264 video")

            await task_manager.redis.hset(f"workflow:{workflow_id}", "first_frame_url", first_frame_url)

            # Continuation face swap: apply Reactor if enabled
            face_swap_config = _get_config(req, "stage2_first_frame", "face_swap", {})
            if face_swap_config.get("enabled") and req.reference_image and first_frame_url:
                logger.info(f"[{workflow_id}] Continuation face swap: applying Reactor to last frame, reference={req.reference_image}")
                # Update heartbeat before long-running face swap to prevent orphan false positive
                await task_manager.redis.hset(f"workflow:{workflow_id}", "executor_heartbeat", str(int(time.time())))
                swapped_url = await _apply_face_swap_via_comfyui(
                    first_frame_url,
                    req.reference_image,
                    strength=face_swap_config.get("strength", 1.0),
                    task_manager=task_manager,
                    workflow_id=workflow_id
                )
                if swapped_url:
                    first_frame_url = swapped_url
                    await task_manager.redis.hset(f"workflow:{workflow_id}", "first_frame_url", first_frame_url)
                    frame_source = f"{frame_source}+face_swap"
                    logger.info(f"[{workflow_id}] Continuation face swap succeeded: {swapped_url}")
                else:
                    logger.warning(f"[{workflow_id}] Continuation face swap failed, using original frame")
            elif face_swap_config.get("enabled") and not req.reference_image:
                logger.warning(f"[{workflow_id}] Continuation face swap enabled but no reference_image")

            await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "completed", details_dict={
                "source": frame_source,
                "parent_workflow_id": req.parent_workflow_id,
                "url": first_frame_url,
                "face_swapped": "+face_swap" in frame_source,
            })
            logger.info(f"[{workflow_id}] Continuation Stage 2: first_frame_url={first_frame_url} (source={frame_source})")
        else:
            await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "running")

            first_frame_url = await _acquire_first_frame(workflow_id, req, analysis_result, task_manager)
            if first_frame_url:
                await task_manager.redis.hset(f"workflow:{workflow_id}", "first_frame_url", first_frame_url)

            # 保存结构化详细信息
            if req.mode == "t2v":
                if first_frame_url:
                    source_text = "pose_reference"  # T2V found a pose -> internal I2V
                else:
                    source_text = "t2v"  # pure T2V
            elif req.mode == "first_frame":
                source_text = "upload"
            else:  # face_reference / full_body_reference
                if first_frame_url and analysis_result and analysis_result.get("reference_image"):
                    source_text = "pose_reference"
                elif first_frame_url and req.reference_image and first_frame_url == req.reference_image:
                    source_text = "reference_image_fallback"
                else:
                    source_text = "unknown"

            details_dict = {
                "source": source_text,
                "url": first_frame_url,
                "face_swapped": False
            }

            # Stage 2.1: 首帧换脸（可选）
            if first_frame_url:
                face_swap_config = _get_config(req, "stage2_first_frame", "face_swap", {})
                logger.info(f"[{workflow_id}] Face swap check: config={face_swap_config}, enabled={face_swap_config.get('enabled')}, reference_image={req.reference_image}")

                # 获取选中图片的 skip_reactor 标记
                skip_reactor_flag = False
                if analysis_result:
                    skip_reactor_flag = analysis_result.get("reference_skip_reactor", False)

                # 预判 SeeDream 是否计划运行
                seedream_planned = False
                if req.mode == "full_body_reference":
                    seedream_planned = True
                elif req.mode == "face_reference":
                    seedream_planned = _get_config(req, "stage3_seedream", "enabled", True)

                reactor_configured = face_swap_config.get("enabled") and req.reference_image
                reactor_deferred = False  # 标记是否因 skip_reactor 而延迟执行

                if reactor_configured:
                    if skip_reactor_flag and seedream_planned:
                        # 图片有遮挡 + SeeDream 会处理 → 跳过 Reactor
                        reactor_deferred = True
                        details_dict["reactor_deferred"] = True
                        details_dict["skip_reactor_flag"] = True
                        logger.info(f"[{workflow_id}] Reactor deferred: skip_reactor flag set, SeeDream will handle")
                    else:
                        # 正常执行 Reactor (via ComfyUI)
                        logger.info(f"[{workflow_id}] Applying face swap to first frame (ComfyUI): {first_frame_url}")
                        swapped_url = await _apply_face_swap_via_comfyui(
                            first_frame_url,
                            req.reference_image,
                            strength=face_swap_config.get("strength", 1.0),
                            task_manager=task_manager,
                            workflow_id=workflow_id
                        )
                        if swapped_url:
                            logger.info(f"[{workflow_id}] Face swap succeeded: {swapped_url}")
                            first_frame_url = swapped_url
                            await task_manager.redis.hset(f"workflow:{workflow_id}", "first_frame_url", first_frame_url)
                            details_dict["face_swapped"] = True
                            details_dict["face_swap_strength"] = face_swap_config.get("strength", 1.0)
                            details_dict["url"] = first_frame_url
                        else:
                            logger.warning(f"[{workflow_id}] Face swap returned None (failed)")
                elif face_swap_config.get("enabled") and not req.reference_image:
                    # Face swap enabled but no reference image provided
                    details_dict["face_swap_skipped"] = True
                    details_dict["face_swap_skip_reason"] = "未提供参考图片 (reference_image)"
                    logger.warning(f"[{workflow_id}] Face swap enabled but no reference_image provided")
                else:
                    logger.info(f"[{workflow_id}] Face swap not enabled or conditions not met")
            else:
                logger.info(f"[{workflow_id}] No first frame (T2V mode), skipping face swap")

            await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "completed", details_dict=details_dict)

        # Stage 3: SeeDream Editing
        await task_manager.redis.hset(f"workflow:{workflow_id}", "current_stage", "seedream_edit")

        if _resumed_stages.get("seedream_edit"):
            # Resume: restore edited_frame_url from Redis
            edited_frame_url = await task_manager.redis.hget(f"workflow:{workflow_id}", "edited_frame_url") or first_frame_url
            logger.info(f"[{workflow_id}] Stage 3 skipped (already completed), edited_frame_url={edited_frame_url}")
        elif is_continuation:
            # Skip Stage 3: use parent's edited frame directly
            edited_frame_url = first_frame_url
            await task_manager.redis.hset(f"workflow:{workflow_id}", "edited_frame_url", edited_frame_url)
            await _update_stage(task_manager, workflow_id, "seedream_edit", "completed", details_dict={
                "skipped": True,
                "reason": "故事续写模式，跳过SeeDream编辑"
            })
            logger.info(f"[{workflow_id}] Continuation Stage 3: skipped")
        else:
            await _update_stage(task_manager, workflow_id, "seedream_edit", "running")

            edited_frame_url = first_frame_url

            # 检查是否需要执行 SeeDream
            should_run_seedream = False
            skip_reason = ""

            if req.mode in ("first_frame", "t2v"):
                # first_frame / T2V 模式跳过 SeeDream
                should_run_seedream = False
                skip_reason = "跳过（首帧/T2V模式）"

            elif req.mode == "full_body_reference":
                # full_body_reference 必须执行 SeeDream
                should_run_seedream = True

            elif req.mode == "face_reference":
                # face_reference 可选执行 SeeDream
                stage3_enabled = _get_config(req, "stage3_seedream", "enabled", True)
                should_run_seedream = stage3_enabled
                if not should_run_seedream:
                    skip_reason = "跳过（未启用）"

            if should_run_seedream and req.reference_image:
                # 根据宽高比计算 SeeDream 分辨率
                # turbo 模式使用视频目标分辨率以加速; 非 turbo 使用 1080p 以保证编辑质量
                try:
                    # 解析宽高比
                    if req.aspect_ratio:
                        ar_parts = req.aspect_ratio.split(':')
                        ar_width = int(ar_parts[0])
                        ar_height = int(ar_parts[1])
                    else:
                        # Derive aspect ratio from resolution config
                        video_resolution = _get_config(req, "stage4_video", "generation", {}).get("resolution", "720p_3_4")
                        if "16_9" in video_resolution or "16:9" in video_resolution:
                            ar_width, ar_height = 16, 9
                        elif "3_4" in video_resolution or "3:4" in video_resolution:
                            ar_width, ar_height = 3, 4
                        else:
                            ar_width, ar_height = 3, 4
                        logger.info(f"[{workflow_id}] SeeDream: aspect_ratio not provided, derived {ar_width}:{ar_height} from resolution {video_resolution}")

                    # 使用视频目标分辨率作为 SeeDream 分辨率，最小 720p 保证编辑质量
                    import re as _re
                    res_str = req.resolution or "480p"
                    _m = _re.match(r'(\d+)', res_str)
                    p_val = int(_m.group(1)) if _m else 480
                    p_val = max(p_val, 720)  # SeeDream 最小 720p，避免低分辨率导致模糊
                    if ar_width >= ar_height:
                        height = round(p_val / 8) * 8
                        width = round(p_val * ar_width / ar_height / 8) * 8
                    else:
                        width = round(p_val / 8) * 8
                        height = round(p_val * ar_height / ar_width / 8) * 8

                    detected_size = f"{width}x{height}"
                    aspect_ratio_str = req.aspect_ratio if req.aspect_ratio else f"{ar_width}:{ar_height}"
                    logger.info(f"[{workflow_id}] SeeDream will use {p_val}p resolution: {detected_size} (aspect ratio: {aspect_ratio_str})")
                except Exception as e:
                    logger.warning(f"Failed to calculate SeeDream size: {e}, using default")
                    detected_size = "832x1216"

                # 保存 SeeDream 参数（在开始时就显示）
                swap_face = _get_config(req, "stage3_seedream", "swap_face", None)
                swap_accessories = _get_config(req, "stage3_seedream", "swap_accessories", None)
                swap_expression = _get_config(req, "stage3_seedream", "swap_expression", None)
                swap_clothing = _get_config(req, "stage3_seedream", "swap_clothing", None)
                edit_mode = _get_config(req, "stage3_seedream", "mode", "face_wearings")
                # Stage 3 reactor is always disabled — face swap is handled in Stage 2 only
                enable_reactor = False
                custom_prompt = _get_config(req, "stage3_seedream", "prompt", None)
                strength = _get_config(req, "stage3_seedream", "strength", 0.8)
                seed = _get_config(req, "stage3_seedream", "seed", None)
                # 优先使用配置的尺寸，如果没有配置则使用检测到的尺寸
                size = _get_config(req, "stage3_seedream", "size", detected_size)

                # Build display prompt
                if swap_face is not None:
                    display_prompt = custom_prompt or get_default_seedream_prompt(
                        swap_face=swap_face, swap_accessories=swap_accessories or False,
                        swap_expression=swap_expression or False, swap_clothing=swap_clothing or False
                    )
                    mode_label = f"custom(face={swap_face},acc={swap_accessories},expr={swap_expression},cloth={swap_clothing})"
                else:
                    display_prompt = custom_prompt or get_default_seedream_prompt(mode=edit_mode)
                    mode_label = edit_mode
                if req.user_prompt:
                    display_prompt = f"{display_prompt}. {req.user_prompt}"

                # 在 running 状态时就保存参数
                running_details = {
                    "mode": mode_label,
                    "swap_face": swap_face,
                    "swap_accessories": swap_accessories,
                    "swap_expression": swap_expression,
                    "swap_clothing": swap_clothing,
                    "enable_reactor": enable_reactor,
                    "prompt": display_prompt,
                    "strength": strength,
                    "seed": seed,
                    "size": size,
                    "reference_image": req.reference_image,
                    "first_frame_url": first_frame_url
                }
                await _update_stage(task_manager, workflow_id, "seedream_edit", "running", details_dict=running_details)

                try:
                    edit_result = await _edit_first_frame(workflow_id, req, first_frame_url, size, task_manager)
                    edited_frame_url = edit_result.url
                    await task_manager.redis.hset(f"workflow:{workflow_id}", "edited_frame_url", edited_frame_url)

                    # 完成时添加结果 URL 和调试信息
                    running_details["url"] = edited_frame_url
                    running_details["model"] = edit_result.model
                    running_details["api_status"] = edit_result.api_status
                    running_details["face_swapped"] = edit_result.face_swapped
                    if edit_result.fallback_used:
                        running_details["fallback_used"] = edit_result.fallback_used
                        running_details["fallback_reason"] = edit_result.fallback_reason
                    if edit_result.error:
                        running_details["error"] = edit_result.error
                    await _update_stage(task_manager, workflow_id, "seedream_edit", "completed", details_dict=running_details)
                except Exception as seedream_exc:
                    running_details["error"] = f"SeeDream 编辑失败: {seedream_exc}"
                    running_details["api_status"] = "failed"
                    await _update_stage(task_manager, workflow_id, "seedream_edit", "failed", details_dict=running_details)
                    if req.mode == "full_body_reference":
                        raise Exception(f"SeeDream editing failed and is required for full_body_reference mode: {seedream_exc}")

                    # face_reference 模式：检查 Reactor 兜底
                    if reactor_deferred and reactor_configured:
                        # skip_reactor 图片 + SeeDream 失败 → Reactor 补救 (via ComfyUI)
                        logger.info(f"[{workflow_id}] SeeDream failed, applying deferred reactor fallback (ComfyUI)")
                        swapped_url = await _apply_face_swap_via_comfyui(
                            first_frame_url,
                            req.reference_image,
                            strength=face_swap_config.get("strength", 1.0),
                            task_manager=task_manager,
                            workflow_id=workflow_id
                        )
                        if swapped_url:
                            edited_frame_url = swapped_url
                            running_details["reactor_fallback"] = True
                        else:
                            edited_frame_url = first_frame_url

                        running_details["fallback_used"] = True
                        running_details["fallback_reason"] = "SeeDream 失败，Reactor 兜底"
                    else:
                        edited_frame_url = first_frame_url
                        running_details["fallback_used"] = True
                        running_details["fallback_reason"] = "SeeDream 调用异常，使用原图"

                    await _update_stage(task_manager, workflow_id, "seedream_edit", "completed", details_dict=running_details)
            else:
                await _update_stage(task_manager, workflow_id, "seedream_edit", "completed", details_dict={"skipped": True, "reason": skip_reason})

        # Stage 4: Video Generation
        await task_manager.redis.hset(f"workflow:{workflow_id}", "current_stage", "video_generation")

        # 获取所有视频生成参数
        video_model = _get_config(req, "stage4_video", "model", "A14B")
        video_resolution = _get_config(req, "stage4_video", "resolution", "480p_3:4")
        video_duration = _get_config(req, "stage4_video", "duration", "5s")

        # 检查视频换脸配置
        video_face_swap_config = _get_config(req, "stage4_video", "face_swap", {})
        face_swap_enabled = video_face_swap_config.get("enabled", False) if isinstance(video_face_swap_config, dict) else False

        # 检查后处理配置
        postprocess_config = _get_config(req, "stage4_video", "postprocess", {})
        upscale_enabled = postprocess_config.get("upscale", {}).get("enabled", False) if isinstance(postprocess_config, dict) else False
        interp_enabled = postprocess_config.get("interpolation", {}).get("enabled", False) if isinstance(postprocess_config, dict) else False
        mmaudio_enabled = postprocess_config.get("mmaudio", {}).get("enabled", False) if isinstance(postprocess_config, dict) else False

        # 在 running 状态时就保存所有参数
        auto_prompt = _get_config(req, "stage1_prompt_analysis", "auto_prompt", True)
        display_prompt = req.user_prompt

        # ── Unified prompt generation (replaces analyze + optimize + refine) ──
        # Stage 1 now skips LLM. The single LLM call happens here, after the
        # first frame is ready, so it can incorporate image context directly.
        if analysis_result and auto_prompt:
            try:
                from api.services.prompt_optimizer import PromptOptimizer

                optimizer = PromptOptimizer()
                # Continuation always has a first frame (parent's last frame) → always I2V
                optimizer_mode = "i2v" if is_continuation else ("t2v" if req.mode == "t2v" else "i2v")
                frame_desc = ""

                # Build pose description (when we have a first frame)
                if optimizer_mode != "t2v":
                    pose_keys = analysis_result.get("pose_keys", [])
                    if pose_keys:
                        try:
                            import sqlite3
                            from pathlib import Path
                            pose_db = Path(__file__).parent.parent.parent / "data" / "wan22.db"
                            conn = sqlite3.connect(str(pose_db))
                            conn.row_factory = sqlite3.Row
                            cursor = conn.cursor()
                            placeholders = ",".join("?" * len(pose_keys))
                            cursor.execute(
                                f"SELECT pose_key, name_en, description FROM poses WHERE pose_key IN ({placeholders})",
                                pose_keys,
                            )
                            descs = []
                            for row in cursor.fetchall():
                                descs.append(f"{row['name_en']}: {row['description']}")
                            cursor.close()
                            conn.close()
                            if descs:
                                frame_desc = "Target action/pose (NOTE: the actual image may NOT yet show this — check VLM description for the real image state): " + "; ".join(descs)
                                logger.info(f"[{workflow_id}] Pose description for prompt gen: {frame_desc[:100]}")
                        except Exception as e:
                            logger.warning(f"[{workflow_id}] Failed to build pose description: {e}")

                    # Download first frame for VLM (first_frame mode only)
                    vlm_succeeded = False
                    if edited_frame_url and req.mode not in ("face_reference", "full_body_reference"):
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.get(edited_frame_url) as resp:
                                    if resp.status == 200:
                                        img_bytes = await resp.read()
                                        img_b64 = base64.b64encode(img_bytes).decode()
                                        vlm_desc = await optimizer._describe_image(img_b64)
                                        if vlm_desc:
                                            vlm_succeeded = True
                                            # Combine VLM visual description with pose semantic context
                                            if frame_desc:
                                                frame_desc = f"ACTUAL IMAGE STATE (from VLM — this is what the image really shows): {vlm_desc}\nTARGET ACTION (user's goal — the image may NOT show this yet): {frame_desc}"
                                            else:
                                                frame_desc = vlm_desc
                                            logger.info(f"[{workflow_id}] VLM first frame desc: {vlm_desc[:120]}")
                                    else:
                                        logger.warning(f"[{workflow_id}] Failed to download first frame: HTTP {resp.status}")
                        except Exception as e:
                            logger.warning(f"[{workflow_id}] VLM description failed: {e}")
                        # If VLM failed but we have pose desc, warn LLM that we don't know actual image state
                        if not vlm_succeeded and frame_desc:
                            frame_desc = f"WARNING: No visual description available (VLM failed). The subject's actual clothing/position is UNKNOWN — assume the subject may be clothed and not yet in position. Apply prerequisite transitions if the target action requires nudity or a specific pose.\n{frame_desc}"
                            logger.info(f"[{workflow_id}] VLM failed, added unknown-state warning to frame_desc")
                    elif not edited_frame_url:
                        logger.info(f"[{workflow_id}] No edited frame URL, skipping VLM")
                    else:
                        logger.info(f"[{workflow_id}] Skipping VLM for {req.mode} mode, using pose description only")

                standin_enabled = _get_config(req, "stage4_video", "standin_enabled", False)

                video_loras = analysis_result.get("video_loras", [])
                # Stand-In mode: skip auto LoRAs to prevent trigger prompts from
                # overriding the Stand-In prompt rules (Stand-In injects its own LoRAs)
                if standin_enabled:
                    video_loras = []
                    logger.info(f"[{workflow_id}] Stand-In mode: cleared video_loras for prompt generation")
                lora_info = _build_lora_context(video_loras)
                trigger_words = _collect_trigger_words(video_loras)

                duration_str = _get_config(req, "stage4_video", "duration", "5s")
                dur_val = float(str(duration_str).replace("s", "")) if duration_str else 5.0

                # For continuations: use the already-generated continuation prompt
                # (which correctly incorporates user intent + parent context),
                # not the raw user_prompt which would be re-interpreted from scratch.
                video_gen_prompt = req.user_prompt
                if is_continuation and analysis_result.get("video_prompt"):
                    video_gen_prompt = analysis_result["video_prompt"]
                    logger.info(f"[{workflow_id}] Using continuation prompt for generate_video_prompt ({len(video_gen_prompt)} chars)")

                result = await optimizer.generate_video_prompt(
                    prompt=video_gen_prompt,
                    trigger_words=trigger_words,
                    mode=optimizer_mode,
                    duration=dur_val,
                    lora_info=lora_info,
                    image_description=frame_desc,
                    standin_mode=bool(standin_enabled),
                )
                final_prompt = result["optimized_prompt"]
                has_prereq = result.get("has_prerequisite", False)
                logger.info(f"[{workflow_id}] generate_video_prompt result (scene={result.get('scene_type')}, prerequisite={has_prereq}): {final_prompt[:150]}")
                analysis_result["video_prompt"] = final_prompt
                analysis_result["optimized_i2v_prompt"] = final_prompt
                analysis_result["has_prerequisite"] = has_prereq
                display_prompt = final_prompt
            except Exception as e:
                logger.warning(f"[{workflow_id}] generate_video_prompt failed, using raw prompt: {e}")
                display_prompt = analysis_result.get("optimized_i2v_prompt") or req.user_prompt
        running_details = {
            "model": video_model,
            "resolution": video_resolution,
            "duration": video_duration,
            "face_swap_enabled": face_swap_enabled,
            "upscale_enabled": upscale_enabled,
            "interpolation_enabled": interp_enabled,
            "mmaudio_enabled": mmaudio_enabled,
            "first_frame_url": edited_frame_url,
            "prompt": display_prompt
        }
        await _update_stage(task_manager, workflow_id, "video_generation", "running", details_dict=running_details)

        chain_id, final_video_url, loras_info = await _generate_video(workflow_id, req, edited_frame_url, analysis_result, task_manager, is_continuation=is_continuation, parent_workflow=parent_workflow, origin_first_frame_url=origin_first_frame_url if is_continuation else None)

        if chain_id:
            await task_manager.redis.hset(f"workflow:{workflow_id}", "chain_id", chain_id)
        if final_video_url:
            # Apply H5-supplied watermark in place before persisting the URL.
            # req.watermark arrives from the H5 proxy (see H5 docs/launch/
            # WATERMARK_CONTRACT.md). None = no watermark — fail-open.
            try:
                watermark_cfg = getattr(req, "watermark", None)
                if watermark_cfg:
                    from api.services import watermark_service
                    local_path = watermark_service.resolve_result_url_to_local_path(final_video_url)
                    if local_path is not None and local_path.exists():
                        await watermark_service.apply_to_video_path(local_path, watermark_cfg)
                    else:
                        logger.info(
                            f"[{workflow_id}] watermark: skipping non-local final_video_url={final_video_url}"
                        )
            except Exception as _wm_err:
                # Fail-open: never let a watermark failure kill the workflow.
                logger.warning(f"[{workflow_id}] watermark apply failed: {_wm_err}")
            await task_manager.redis.hset(f"workflow:{workflow_id}", "final_video_url", final_video_url)

        # 完成时添加结果和 LoRA 信息
        running_details["chain_id"] = chain_id
        running_details["video_url"] = final_video_url
        if loras_info:
            running_details["loras"] = loras_info
            # 计算完整 prompt（含 trigger words），用于前端展示
            from api.models.schemas import LoraInput as _LoraInput
            from api.services.workflow_builder import _inject_trigger_words as _itw
            _lora_objs = [_LoraInput(name=l["name"], strength=l["strength"], trigger_words=l.get("trigger_words") or [], trigger_prompt=l.get("trigger_prompt")) for l in loras_info]
            running_details["prompt"] = _itw(running_details["prompt"], _lora_objs)

        if not final_video_url:
            # Video generation failed — mark stage and workflow as failed
            running_details["error"] = "视频生成失败，未返回视频URL"
            await _update_stage(task_manager, workflow_id, "video_generation", "failed", details_dict=running_details)
            await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
                "status": "failed",
                "error": running_details["error"]
            })
            logger.error(f"Workflow {workflow_id} failed: no video URL returned")
            return

        await _update_stage(task_manager, workflow_id, "video_generation", "completed", details_dict=running_details)

        # M2: Check if workflow was cancelled during execution before overwriting status
        import time as _time
        current_status = await task_manager.redis.hget(f"workflow:{workflow_id}", "status")
        if current_status in ("cancelled", "failed"):
            logger.info(f"Workflow {workflow_id} was {current_status} during execution, not overwriting")
            return
        # Propagate lossless last frame from chain to workflow for future continuations
        if chain_id:
            try:
                chain_data = await task_manager.redis.hgetall(f"chain:{chain_id}")
                lossless_last_frame = chain_data.get("lossless_last_frame_url", "")
                if lossless_last_frame:
                    await task_manager.redis.hset(f"workflow:{workflow_id}", "lossless_last_frame_url", lossless_last_frame)
                    logger.info(f"[{workflow_id}] Propagated lossless frame to workflow: {lossless_last_frame}")
            except Exception as e:
                logger.warning(f"[{workflow_id}] Failed to propagate lossless frame: {e}")

        # Save origin_first_frame_url for future continuations to use directly
        completion_mapping = {
            "status": "completed",
            "completed_at": str(int(_time.time()))
        }
        if is_continuation:
            if origin_first_frame_url:
                completion_mapping["origin_first_frame_url"] = origin_first_frame_url
            completion_mapping["continuation_index"] = str(continuation_index)
        elif not is_continuation:
            # For root workflows, the origin is their own first frame
            # For T2V roots (no input image), fallback to lossless last frame
            own_first_frame = (
                await task_manager.redis.hget(f"workflow:{workflow_id}", "first_frame_url")
                or await task_manager.redis.hget(f"workflow:{workflow_id}", "lossless_last_frame_url")
            )
            if own_first_frame:
                completion_mapping["origin_first_frame_url"] = own_first_frame
        await task_manager.redis.hset(f"workflow:{workflow_id}", mapping=completion_mapping)
        # M1: Refresh TTL on completion
        from api.config import TASK_EXPIRY
        await task_manager.redis.expire(f"workflow:{workflow_id}", TASK_EXPIRY)
        logger.info(f"Workflow {workflow_id} completed successfully")

    except Exception as e:
        logger.error(f"Workflow {workflow_id} failed: {e}", exc_info=True)
        import time as _time
        # M2: Don't overwrite cancelled status
        current_status = await task_manager.redis.hget(f"workflow:{workflow_id}", "status")
        if current_status == "cancelled":
            logger.info(f"Workflow {workflow_id} was cancelled, not overwriting with failed")
            return
        # Mark the current running stage as failed so it doesn't stay in "running"
        try:
            current_stage = await task_manager.redis.hget(f"workflow:{workflow_id}", "current_stage")
            if current_stage:
                await _update_stage(task_manager, workflow_id, current_stage, "failed",
                                    details_dict={"error": str(e)})
        except Exception:
            pass
        await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
            "status": "failed",
            "error": str(e),
            "completed_at": str(int(_time.time()))
        })
        # M1: Refresh TTL on failure
        try:
            from api.config import TASK_EXPIRY
            await task_manager.redis.expire(f"workflow:{workflow_id}", TASK_EXPIRY)
        except Exception:
            pass


async def _update_stage(task_manager, workflow_id: str, stage_name: str, status: str, error: str = None, details: str = None, details_dict: dict = None):
    """Update stage status in Redis

    Args:
        details: Legacy text details (will be converted to dict if details_dict not provided)
        details_dict: Structured details as dict (preferred)
    """
    mapping = {
        f"stage_{stage_name}": status,
        "executor_heartbeat": str(int(time.time())),  # Orphan recovery checks this
    }
    if error:
        mapping[f"stage_{stage_name}_error"] = error
    if details_dict:
        # Use structured dict (preferred)
        mapping[f"stage_{stage_name}_details"] = json.dumps(details_dict, ensure_ascii=False)
    elif details:
        # Legacy text details - save as-is for backward compatibility
        mapping[f"stage_{stage_name}_details"] = details
    await task_manager.redis.hset(f"workflow:{workflow_id}", mapping=mapping)


# ── Cached instagirl_v2 metadata (loaded once from DB) ──────────────────────
_instagirl_cache: Optional[dict] = None


def _load_instagirl_metadata() -> dict:
    """Load instagirl_v2 metadata from MySQL (cached after first call).

    Returns dict with keys: name, trigger_words, trigger_prompt, example_prompts, description.
    """
    global _instagirl_cache
    if _instagirl_cache is not None:
        return _instagirl_cache

    fallback = {
        "name": "instagirl_v2",
        "trigger_words": [],
        "trigger_prompt": None,
        "example_prompts": [],
        "description": "",
    }
    try:
        import pymysql
        import json as _json
        from api.routes.recommend import DB_CONFIG

        conn = pymysql.connect(**DB_CONFIG)
        try:
            cur = conn.cursor(pymysql.cursors.DictCursor)
            cur.execute(
                "SELECT trigger_words, trigger_prompt, description, example_prompts "
                "FROM lora_metadata WHERE name = %s",
                ("instagirl_v2",),
            )
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()

        if not row:
            logger.warning("instagirl_v2 not found in lora_metadata")
            _instagirl_cache = fallback
            return _instagirl_cache

        # trigger_words
        trigger_words = []
        raw_tw = row.get("trigger_words")
        if isinstance(raw_tw, str):
            try:
                parsed = _json.loads(raw_tw)
                if isinstance(parsed, list):
                    trigger_words = parsed
            except Exception:
                trigger_words = [raw_tw] if raw_tw.strip() else []
        elif isinstance(raw_tw, list):
            trigger_words = raw_tw

        # example_prompts
        raw_ep = row.get("example_prompts")
        if isinstance(raw_ep, str):
            try:
                raw_ep = _json.loads(raw_ep)
            except Exception:
                raw_ep = []
        example_prompts = list(raw_ep) if raw_ep else []

        trigger_prompt = row.get("trigger_prompt") or None
        if trigger_prompt and trigger_prompt.strip() not in example_prompts:
            example_prompts.insert(0, trigger_prompt.strip())

        _instagirl_cache = {
            "name": "instagirl_v2",
            "trigger_words": trigger_words,
            "trigger_prompt": trigger_prompt,
            "example_prompts": example_prompts,
            "description": row.get("description") or "",
        }
        logger.info(f"Loaded instagirl_v2 metadata (trigger_words={trigger_words}, examples={len(example_prompts)})")
        return _instagirl_cache

    except Exception as e:
        logger.warning(f"Failed to load instagirl_v2 metadata: {e}")
        _instagirl_cache = fallback
        return _instagirl_cache


# ── Step 2: Unified LoRA selection ──────────────────────────────────────────

def _select_loras_from_pose(pose_configs) -> tuple[list[dict], list[dict]]:
    """Extract and deduplicate image_loras and video_loras from pose configs.

    Args:
        pose_configs: list of PoseConfig objects from pose_matcher

    Returns: (image_loras, video_loras) — each is a list of dicts with
             lora_id, name, weight, trigger_words, trigger_prompt, noise_stage.
    """
    all_image_loras = []
    all_video_loras = []
    for config in pose_configs:
        all_image_loras.extend(config.image_loras)
        all_video_loras.extend(config.video_loras)

    # Deduplicate image_loras by lora_id
    image_loras_dict = {}
    for lora in all_image_loras:
        lora_id = lora.get('lora_id')
        if lora_id and lora_id not in image_loras_dict and lora.get('enabled', True):
            image_loras_dict[lora_id] = lora

    # Deduplicate video_loras by lora_name (avoids same file stacking)
    video_loras_dict = {}
    for lora in all_video_loras:
        lora_id = lora.get('lora_id')
        lora_name = lora.get('lora_name', '')
        dedup_key = lora_name or lora_id
        if dedup_key and dedup_key not in video_loras_dict and lora.get('enabled', True):
            video_loras_dict[dedup_key] = lora

    def _normalize(lora_raw: dict) -> dict:
        return {
            "lora_id": lora_raw.get("lora_id"),
            "name": lora_raw.get("lora_name", ""),
            "weight": lora_raw.get("recommended_weight", 1.0),
            "trigger_words": lora_raw.get("trigger_words") or [],
            "trigger_prompt": lora_raw.get("trigger_prompt") or None,
            "noise_stage": lora_raw.get("noise_stage") or None,
        }

    image_loras = [_normalize(l) for l in image_loras_dict.values()]
    video_loras = [_normalize(l) for l in video_loras_dict.values()]
    return image_loras, video_loras


# ── Step 3: Default LoRA 补全 ───────────────────────────────────────────────

def _ensure_default_loras(video_loras: list[dict], mode: str, is_continuation: bool = False) -> list[dict]:
    """Ensure default LoRAs are present.

    Rules (all in one place):
    - T2V only (NOT continuations): add instagirl_v2 to video_loras
      - weight 0.4 if other video LoRAs already exist
      - weight 0.8 if no other video LoRAs

    Args:
        video_loras: current video LoRA list (will NOT be mutated)
        mode: request mode ("t2v" / "first_frame" / "face_reference" / "full_body_reference")
        is_continuation: True if this is a cross-workflow continuation (actually I2V)

    Returns: updated video_loras list
    """
    # Continuations are actually I2V (use parent's last frame), skip person-style LoRA
    if mode != "t2v" or is_continuation:
        return video_loras

    # Check if instagirl_v2 is already present
    if any(l.get("name") == "instagirl_v2" for l in video_loras):
        return video_loras

    meta = _load_instagirl_metadata()
    weight = 0.3 if video_loras else 0.6
    instagirl_lora = {
        "lora_id": None,
        "name": "instagirl_v2",
        "weight": weight,
        "trigger_words": meta["trigger_words"],
        "trigger_prompt": meta["trigger_prompt"],
        "noise_stage": None,
    }
    logger.info(f"Default LoRA: adding instagirl_v2 to video_loras (weight={weight}, has_other_loras={bool(video_loras)})")
    return video_loras + [instagirl_lora]


# ── Step 4: Video prompt generation ─────────────────────────────────────────

def _build_lora_context(video_loras: list[dict]) -> Optional[list[dict]]:
    """Build lora_info list for PromptOptimizer from video_loras."""
    if not video_loras:
        return None
    lora_info = []
    for lora in video_loras:
        tw = lora.get("trigger_words") or []
        if isinstance(tw, str):
            import json as _json
            try:
                tw = _json.loads(tw)
            except Exception:
                tw = []
        lora_info.append({
            "name": lora.get("name", ""),
            "description": ", ".join(tw) if tw else lora.get("description", ""),
            "trigger_prompt": (lora.get("trigger_prompt") or "").strip(),
            "example_prompts": list(lora.get("example_prompts") or []),
        })
    return lora_info


def _collect_trigger_words(loras: list[dict]) -> list[str]:
    """Flatten trigger_words from a list of LoRA dicts."""
    words = []
    for lora in loras:
        tw = lora.get("trigger_words") or []
        if isinstance(tw, str):
            import json as _json
            try:
                tw = _json.loads(tw)
            except Exception:
                tw = []
        for w in (tw or []):
            if w and w not in words:
                words.append(w)
    return words


async def _build_video_prompt(user_prompt: str, mode: str, video_loras: list[dict], skip_llm: bool) -> str:
    """Return raw user prompt + trigger words.  LLM optimization is deferred to
    generate_video_prompt() which runs after the first frame is ready.

    Args:
        user_prompt: original user prompt
        mode: request mode ("t2v" / "first_frame" / "face_reference" / "full_body_reference")
        video_loras: selected video LoRAs (with trigger_words, example_prompts, etc.)
        skip_llm: True to skip LLM optimization (turbo mode)

    Returns: user prompt with trigger words appended (no LLM call)
    """
    trigger_words = _collect_trigger_words(video_loras)
    if trigger_words:
        return f"{user_prompt}, {', '.join(trigger_words)}"
    return user_prompt


# ── Step 5: T2I prompt generation ───────────────────────────────────────────

T2I_POSITIVE_TAGS = (
    "masterpiece, best quality, ultra detailed, high resolution, "
    "sharp focus, realistic, photorealistic"
)

T2I_NEGATIVE_TAGS = (
    "low quality, blurry, distorted, deformed, bad anatomy, bad hands, "
    "extra fingers, ugly, cartoon, anime, painting, drawing, "
    "overexposed, oversaturated, plastic skin, airbrushed, "
    "glossy skin, shiny skin, doll, cropped, watermark, text"
)


def _build_t2i_prompt(user_prompt: str, image_loras: list[dict], optimized_base: Optional[str] = None) -> tuple[str, str]:
    """Build T2I positive and negative prompts (template-based, no LLM).

    Positive = [quality tags] + [trigger_words/trigger_prompt] + [base prompt] + [<lora:id:weight> tags]
    Negative = [quality negative tags]

    Args:
        user_prompt: original user prompt
        image_loras: selected image LoRAs
        optimized_base: optional LLM-optimized base prompt (used instead of user_prompt if provided)

    Returns: (positive_prompt, negative_prompt)
    """
    import json as _json

    base = optimized_base if optimized_base else user_prompt

    trigger_parts = []
    lora_tags = []
    for lora in image_loras[:3]:
        # trigger_words
        tw = lora.get("trigger_words") or []
        if isinstance(tw, str):
            try:
                tw = _json.loads(tw)
            except Exception:
                tw = []
        for word in (tw or []):
            if word and word not in trigger_parts:
                trigger_parts.append(word)
        # trigger_prompt
        tp = lora.get("trigger_prompt")
        if tp and tp.strip() and tp.strip() not in trigger_parts:
            trigger_parts.append(tp.strip())
        # SD WebUI lora tag — use lora_id for Forge
        lora_id = lora.get("lora_id", "")
        lora_weight = lora.get("weight", 0.8)
        if lora_id:
            lora_tags.append(f"<lora:{lora_id}:{lora_weight}>")

    # Assemble positive
    parts = [T2I_POSITIVE_TAGS]
    if trigger_parts:
        parts.append(", ".join(trigger_parts))
    parts.append(base)
    positive = ", ".join(parts)
    if lora_tags:
        positive = positive + " " + " ".join(lora_tags)

    return positive, T2I_NEGATIVE_TAGS


# ── Main: _analyze_prompt (5-step orchestration) ───────────────────────────

async def _analyze_prompt(req, task_manager) -> Optional[dict]:
    """Stage 1 — unified prompt analysis & LoRA selection.

    5 steps:
    1. Pose matching (auto-recommend if not provided)
    2. LoRA selection (from pose DB)
    3. Default LoRA 补全 (instagirl_v2 for T2V)
    4. Video prompt generation (LLM, t2v/i2v aware)
    5. T2I prompt generation (template, quality tags + LoRA tags)

    Returns analysis_result dict.
    """
    try:
        auto_completion = int(_get_config(req, "stage1_prompt_analysis", "auto_completion", 2))
        auto_prompt = _get_config(req, "stage1_prompt_analysis", "auto_prompt", True)
        skip_llm = not auto_prompt  # turbo mode: skip LLM optimization and reranking
        if auto_completion < 1:
            skip_llm = True  # Level 0: skip LLM prompt optimization

        # ── Step 1: Pose matching ───────────────────────────────────────
        pose_keys = req.pose_keys
        if not pose_keys and auto_completion >= 2:
            from api.routes.poses import recommend_poses_by_prompt, PoseRecommendRequest
            try:
                pose_min_score = 0.5 if req.mode in (None, "t2v", "first_frame") else 0.3
                pose_req = PoseRecommendRequest(prompt=req.user_prompt, top_k=5, use_llm=not skip_llm, min_score=pose_min_score)
                pose_result = await recommend_poses_by_prompt(pose_req, _=None)
                if pose_result.recommendations:
                    selected_pose = pose_result.recommendations[0]
                    pose_keys = [selected_pose.pose_key]
                    logger.info(f"Auto-selected pose: {selected_pose.pose_key} (score: {selected_pose.score:.3f}, llm_rerank={not skip_llm})")
            except Exception as e:
                logger.warning(f"Auto pose recommendation failed: {e}")
        elif not pose_keys:
            logger.info(f"Pose matching skipped (auto_completion={auto_completion} < 2)")

        # ── Step 2: LoRA selection ──────────────────────────────────────
        image_loras = []
        video_loras = []
        reference_image = None
        reference_skip_reactor = False

        if pose_keys:
            from api.services.pose_matcher import get_pose_matcher
            matcher = get_pose_matcher()

            pose_configs = []
            poses = matcher.list_all_poses()
            for pose_key in pose_keys:
                pose = next((p for p in poses if p['pose_key'] == pose_key), None)
                if pose:
                    config = matcher.get_pose_config(pose['id'], {})
                    if config:
                        pose_configs.append(config)

            if pose_configs:
                image_loras, video_loras = _select_loras_from_pose(pose_configs)

                # Select reference image
                all_reference_images = []
                for config in pose_configs:
                    all_reference_images.extend(config.reference_images)
                if all_reference_images:
                    import random
                    selected_ref = random.choice(all_reference_images)
                    reference_image = selected_ref.get('image_url')
                    reference_skip_reactor = bool(selected_ref.get('skip_reactor', 0))

                logger.info(f"Pose LoRA selection: {len(image_loras)} image, {len(video_loras)} video, ref_image={'yes' if reference_image else 'no'}")

        # ── Step 2b: Semantic search fallback when pose yields no video LoRAs ──
        if not video_loras and auto_completion >= 2:
            try:
                from api.services.embedding_service import get_embedding_service
                embedding_service = get_embedding_service()
                search_results = await embedding_service.search_similar_loras(
                    query=req.user_prompt,
                    top_k=5,
                )
                MIN_LORA_SIMILARITY = 0.65
                candidate_ids = [sr["lora_id"] for sr in search_results if sr.get("similarity", 0) >= MIN_LORA_SIMILARITY]

                if candidate_ids:
                    # Determine expected LoRA mode: t2v→T2V, everything else→I2V
                    expected_mode = "T2V" if req.mode in (None, "t2v") else "I2V"
                    sim_map = {sr["lora_id"]: sr.get("similarity", 0) for sr in search_results}

                    import pymysql
                    import json as _json2
                    from api.routes.recommend import DB_CONFIG
                    conn = pymysql.connect(**DB_CONFIG)
                    try:
                        cursor = conn.cursor(pymysql.cursors.DictCursor)
                        placeholders = ','.join(['%s'] * len(candidate_ids))
                        cursor.execute(
                            f"SELECT id, name, mode, trigger_words, trigger_prompt, noise_stage "
                            f"FROM lora_metadata WHERE id IN ({placeholders}) "
                            f"AND (enabled = 1 OR enabled = TRUE) AND mode = %s",
                            candidate_ids + [expected_mode],
                        )
                        rows = cursor.fetchall()
                        cursor.close()
                    finally:
                        conn.close()

                    rows.sort(key=lambda r: sim_map.get(r["id"], 0), reverse=True)
                    if rows:
                        best = rows[0]
                        tw = best.get("trigger_words")
                        if isinstance(tw, str):
                            try:
                                tw = _json2.loads(tw)
                            except Exception:
                                tw = []
                        video_loras.append({
                            "lora_id": best["id"],
                            "name": best.get("name", ""),
                            "weight": 0.8,
                            "trigger_words": tw or [],
                            "trigger_prompt": best.get("trigger_prompt") or None,
                            "noise_stage": best.get("noise_stage"),
                        })
                        logger.info(f"Semantic LoRA fallback: selected '{best.get('name')}' (id={best['id']}, mode={expected_mode}, sim={sim_map.get(best['id'], 0):.3f})")
                    else:
                        logger.info(f"Semantic LoRA fallback: no {expected_mode} LoRA above {MIN_LORA_SIMILARITY}")
                else:
                    logger.info(f"Semantic LoRA fallback: no match above {MIN_LORA_SIMILARITY}")
            except Exception as e:
                logger.warning(f"Semantic LoRA fallback failed: {e}")

        # Fetch preview_url + enrich trigger data from MySQL
        all_lora_ids = [l["lora_id"] for l in image_loras + video_loras if l.get("lora_id")]
        if all_lora_ids:
            try:
                import pymysql
                import json as _json
                from api.routes.recommend import DB_CONFIG
                conn = pymysql.connect(**DB_CONFIG)
                try:
                    cursor = conn.cursor(pymysql.cursors.DictCursor)
                    placeholders = ','.join(['%s'] * len(all_lora_ids))
                    cursor.execute(
                        f"SELECT id, preview_url, trigger_words, trigger_prompt, example_prompts "
                        f"FROM lora_metadata WHERE id IN ({placeholders})",
                        all_lora_ids,
                    )
                    meta_map = {row['id']: row for row in cursor.fetchall()}
                    cursor.close()
                finally:
                    conn.close()

                for l in image_loras + video_loras:
                    meta = meta_map.get(l['lora_id'])
                    if not meta:
                        continue
                    l['preview_url'] = meta.get('preview_url')
                    # Enrich trigger_words / trigger_prompt / example_prompts from DB
                    if not l.get('trigger_words'):
                        raw_tw = meta.get('trigger_words')
                        if isinstance(raw_tw, str):
                            try:
                                l['trigger_words'] = _json.loads(raw_tw)
                            except Exception:
                                l['trigger_words'] = []
                        elif isinstance(raw_tw, list):
                            l['trigger_words'] = raw_tw
                    if not l.get('trigger_prompt'):
                        l['trigger_prompt'] = meta.get('trigger_prompt') or None
                    if not l.get('example_prompts'):
                        raw_ep = meta.get('example_prompts')
                        if isinstance(raw_ep, str):
                            try:
                                l['example_prompts'] = _json.loads(raw_ep)
                            except Exception:
                                l['example_prompts'] = []
                        elif isinstance(raw_ep, list):
                            l['example_prompts'] = raw_ep
            except Exception as e:
                logger.warning(f"Failed to fetch lora metadata: {e}")

        # ── Step 3: Default LoRA 补全 ───────────────────────────────────
        is_cont = bool(getattr(req, 'parent_workflow_id', None))
        if auto_completion >= 2:
            video_loras = _ensure_default_loras(video_loras, req.mode, is_continuation=is_cont)
        else:
            logger.info(f"Default LoRA injection skipped (auto_completion={auto_completion} < 2)")

        # ── Step 4: Video prompt (LLM, t2v/i2v aware) ──────────────────
        video_prompt = await _build_video_prompt(req.user_prompt, req.mode, video_loras, skip_llm)

        # ── Step 5: T2I prompt (template-based) ────────────────────────
        t2i_prompt, t2i_negative_prompt = _build_t2i_prompt(req.user_prompt, image_loras)

        # ── Build result ────────────────────────────────────────────────
        return {
            # Prompts — separated by usage
            "video_prompt": video_prompt,
            "t2i_prompt": t2i_prompt,
            "t2i_negative_prompt": t2i_negative_prompt,

            # LoRAs — separated by usage
            "image_loras": image_loras,
            "video_loras": video_loras,

            # Reference
            "reference_image": reference_image,
            "reference_skip_reactor": reference_skip_reactor,

            # Metadata
            "pose_keys": pose_keys or [],
            "original_prompt": req.user_prompt,

            # Backward compatibility (downstream stages read these keys)
            "optimized_prompt": video_prompt,
            "optimized_i2v_prompt": video_prompt,
            "optimized_t2i_prompt": req.user_prompt,
            "images": [{"url": reference_image, "skip_reactor": reference_skip_reactor}] if reference_image else [],
        }

    except Exception as e:
        logger.error(f"Prompt analysis failed: {e}", exc_info=True)
        return {"_error": str(e)}


async def _find_base_image(workflow_id: str, req, analysis_result: Optional[dict], task_manager) -> Optional[str]:
    """
    Unified base image acquisition logic.
    Priority: 1) pose reference_image -> 2) user reference_image fallback (ref modes only)

    Returns: URL of base image, or None for pure T2V
    """
    from api.services.video_frame_extractor import convert_video_url_to_frame

    # 1) Pose reference image (shared by all modes)
    if analysis_result and analysis_result.get("reference_image"):
        pose_url = analysis_result["reference_image"]
        logger.info(f"[{workflow_id}] Base image from pose reference: {pose_url}")
        # Convert video URL to frame if needed (pose library may contain video URLs)
        pose_url = await convert_video_url_to_frame(pose_url)
        return pose_url

    # 2) Fallback for reference modes: use user's reference_image as base
    if req.mode in ("face_reference", "full_body_reference"):
        if req.reference_image:
            logger.info(f"[{workflow_id}] No pose match, using user reference_image as base")
            return req.reference_image

    # T2V mode or no base image found -> pure T2V
    logger.info(f"[{workflow_id}] No base image found, will use pure T2V")
    return None


async def _process_uploaded_first_frame(workflow_id: str, req, task_manager) -> str:
    """Process uploaded first frame (base64, URL, or path) and return a usable URL."""
    from api.services import storage
    from api.services.video_frame_extractor import convert_video_url_to_frame

    if req.uploaded_first_frame.startswith('data:image'):
        # Base64 data URL
        image_b64 = req.uploaded_first_frame.split(',')[1]
        image_data = base64.b64decode(image_b64)
        filename = f"first_frame_{workflow_id}.png"
        local_path, url = await storage.save_upload(image_data, filename)
        return url
    elif req.uploaded_first_frame.startswith('http://') or req.uploaded_first_frame.startswith('https://'):
        # Remote URL — if it's a video, extract the first frame
        frame_url = await convert_video_url_to_frame(req.uploaded_first_frame)
        if frame_url != req.uploaded_first_frame:
            logger.info(f"[{workflow_id}] Extracted first frame from video URL: {req.uploaded_first_frame}")
            return frame_url
        # Otherwise download the image
        async with aiohttp.ClientSession() as session:
            async with session.get(req.uploaded_first_frame) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to download uploaded frame: {resp.status}")
                image_data = await resp.read()
        filename = f"first_frame_{workflow_id}.png"
        local_path, url = await storage.save_upload(image_data, filename)
        return url
    elif req.uploaded_first_frame.startswith('/api/v1/'):
        logger.info(f"[{workflow_id}] Using already uploaded image: {req.uploaded_first_frame}")
        return req.uploaded_first_frame
    elif req.uploaded_first_frame.startswith('/uploads/'):
        converted_path = '/api/v1' + req.uploaded_first_frame
        logger.info(f"[{workflow_id}] Converting {req.uploaded_first_frame} to {converted_path}")
        return converted_path
    elif '/' not in req.uploaded_first_frame and '.' in req.uploaded_first_frame:
        logger.info(f"[{workflow_id}] Using local filename: {req.uploaded_first_frame}")
        return req.uploaded_first_frame
    else:
        # Fallback: try base64 decode
        try:
            image_data = base64.b64decode(req.uploaded_first_frame)
            filename = f"first_frame_{workflow_id}.png"
            local_path, url = await storage.save_upload(image_data, filename)
            return url
        except Exception as e:
            raise Exception(f"Invalid uploaded_first_frame format: not a data URL, http URL, file path, or valid base64. Error: {e}")


async def _acquire_first_frame(workflow_id: str, req, analysis_result: Optional[dict], task_manager) -> Optional[str]:
    """
    Acquire first frame based on mode.

    Returns: URL of the first frame image, or None for pure T2V
    """
    try:
        if req.mode == "t2v":
            # T2V: skip pose search, always use pure text-to-video
            logger.info(f"[{workflow_id}] T2V mode: skipping base image search, pure T2V")
            return None

        if req.mode == "first_frame":
            # I2V: user provided uploaded_first_frame
            if req.uploaded_first_frame:
                return await _process_uploaded_first_frame(workflow_id, req, task_manager)
            # No upload — shouldn't happen (auto-converted to t2v), but handle gracefully
            logger.warning(f"[{workflow_id}] first_frame mode without upload, falling back to _find_base_image")
            return await _find_base_image(workflow_id, req, analysis_result, task_manager)

        if req.mode in ("face_reference", "full_body_reference"):
            # Reference modes: use unified base image search (pose -> recommend -> reference_image)
            return await _find_base_image(workflow_id, req, analysis_result, task_manager)

        raise Exception(f"Unknown mode: {req.mode}")

    except Exception as e:
        logger.error(f"[{workflow_id}] First frame acquisition failed: {e}", exc_info=True)
        raise


async def _generate_t2i_image(req, analysis_result: Optional[dict], task_manager) -> Optional[str]:
    """
    Generate T2I image using SD WebUI + PONY NSFW model.

    Returns: URL of the generated image
    """
    try:
        from api.config import FORGE_URL
        from api.services import storage
        import aiohttp
        import uuid

        # Use pre-built T2I prompt from Stage 1 analysis (includes quality tags + LoRA tags)
        if analysis_result and analysis_result.get("t2i_prompt"):
            prompt = analysis_result["t2i_prompt"]
            negative_prompt = analysis_result.get("t2i_negative_prompt", T2I_NEGATIVE_TAGS)
            logger.info(f"T2I: using pre-built prompt from analysis_result")
        else:
            prompt = req.user_prompt
            negative_prompt = T2I_NEGATIVE_TAGS

        # Get T2I parameters from internal_config or legacy params
        t2i_config = _get_config(req, "stage2_first_frame", "t2i", {})
        if not t2i_config and req.t2i_params:
            t2i_config = req.t2i_params

        # IMPORTANT: Use high resolution (1080p) for T2I generation
        # Calculate dimensions based on aspect ratio, maintaining 1080p quality
        if req.aspect_ratio:
            ar_parts = req.aspect_ratio.split(':')
            ar_width = int(ar_parts[0])
            ar_height = int(ar_parts[1])
        else:
            # Derive aspect ratio from resolution config
            video_resolution = _get_config(req, "stage4_video", "generation", {}).get("resolution", "720p_3_4")
            if "16_9" in video_resolution or "16:9" in video_resolution:
                ar_width, ar_height = 16, 9
            elif "3_4" in video_resolution or "3:4" in video_resolution:
                ar_width, ar_height = 3, 4
            else:
                ar_width, ar_height = 3, 4
            logger.info(f"T2I: aspect_ratio not provided, derived {ar_width}:{ar_height} from resolution {video_resolution}")

        # 基于 1080p 计算尺寸
        base_width = 1920
        base_height = 1080

        if ar_width / ar_height > base_width / base_height:
            width = base_width
            height = round(base_width * ar_height / ar_width)
        else:
            height = base_height
            width = round(base_height * ar_width / ar_height)

        # 确保是 8 的倍数
        width = round(width / 8) * 8
        height = round(height / 8) * 8

        steps = t2i_config.get("steps", 20)
        cfg_scale = t2i_config.get("cfg_scale", 7.0)
        sampler = t2i_config.get("sampler", "DPM++ 2M Karras")
        seed = t2i_config.get("seed", -1)

        logger.info(f"T2I generation at high resolution: {width}x{height}")

        # Call SD WebUI txt2img API
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "sampler_name": sampler,
            "seed": seed
        }

        logger.info(f"Generating T2I image with prompt: {prompt[:150]}...")

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{FORGE_URL}/sdapi/v1/txt2img", json=payload, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status != 200:
                    raise Exception(f"SD WebUI txt2img failed: {resp.status}")

                result = await resp.json()
                image_b64 = result["images"][0]
                image_data = base64.b64decode(image_b64)

                # Save image
                filename = f"t2i_{uuid.uuid4().hex[:8]}.png"
                local_path, url = await storage.save_upload(image_data, filename)

                logger.info(f"T2I image generated: {url}")
                return url

    except Exception as e:
        logger.error(f"T2I generation failed: {e}", exc_info=True)
        return None


async def _edit_first_frame(workflow_id: str, req, first_frame_url: str, size: str, task_manager) -> Optional[str]:
    """
    Edit first frame using SeeDream.

    Args:
        workflow_id: Workflow ID
        req: Request object
        first_frame_url: URL of the first frame image
        size: Image size (e.g., "832x1216")
        task_manager: Task manager instance

    Returns: URL of the edited image
    """
    try:
        from api.routes.workflow import seedream_edit, SeeDreamEditRequest

        # Stage 3 reactor is always disabled — face swap is handled in Stage 2 only
        enable_reactor = False
        custom_prompt = _get_config(req, "stage3_seedream", "prompt", None)
        strength = _get_config(req, "stage3_seedream", "strength", 0.8)
        seed = _get_config(req, "stage3_seedream", "seed", None)

        # Get toggle values (new) or fall back to legacy mode
        swap_face = _get_config(req, "stage3_seedream", "swap_face", None)
        swap_accessories = _get_config(req, "stage3_seedream", "swap_accessories", None)
        swap_expression = _get_config(req, "stage3_seedream", "swap_expression", None)
        swap_clothing = _get_config(req, "stage3_seedream", "swap_clothing", None)

        if swap_face is not None:
            # New toggle-based mode
            prompt = custom_prompt or get_default_seedream_prompt(
                swap_face=swap_face,
                swap_accessories=swap_accessories or False,
                swap_expression=swap_expression or False,
                swap_clothing=swap_clothing or False
            )
        else:
            # Legacy mode-based
            edit_mode = _get_config(req, "stage3_seedream", "mode", "face_wearings")
            prompt = custom_prompt or get_default_seedream_prompt(mode=edit_mode)

        if req.user_prompt:
            prompt = f"{prompt}. {req.user_prompt}"

        # Call SeeDream edit endpoint
        edit_req = SeeDreamEditRequest(
            scene_image=first_frame_url,
            reference_face=req.reference_image,
            swap_face=swap_face,
            swap_accessories=swap_accessories,
            swap_expression=swap_expression,
            swap_clothing=swap_clothing,
            enable_face_swap=False,  # Stage 3 never does reactor; face swap is Stage 2 only
            prompt=prompt,
            size=size,
            seed=seed
        )

        result = await seedream_edit(edit_req, _=None)
        return result  # Return full result object with debug info

    except Exception as e:
        logger.error(f"SeeDream editing failed: {e}", exc_info=True)
        raise


async def _generate_video(workflow_id: str, req, first_frame_url: Optional[str], analysis_result: Optional[dict], task_manager, is_continuation: bool = False, parent_workflow: dict = None, origin_first_frame_url: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """
    Generate video using Chain workflow.

    Returns: (chain_id, final_video_url)
    """
    try:
        logger.info(f"[{workflow_id}] _generate_video started, first_frame_url={first_frame_url}")
        from api.routes.extend import generate_chain
        from api.models.schemas import AutoChainRequest, ChainSegment, LoraInput, ImageMode
        from api.models.enums import ModelType
        import aiohttp
        from io import BytesIO
        from fastapi import UploadFile

        # Pre-fetch first frame in background (runs in parallel with parameter setup)
        _prefetch_task = None
        if first_frame_url and (first_frame_url.startswith('http://') or first_frame_url.startswith('https://')):
            async def _prefetch_first_frame(url):
                try:
                    from api.services.video_frame_extractor import convert_video_url_to_frame
                    url = await convert_video_url_to_frame(url)
                    async with aiohttp.ClientSession() as s:
                        async with s.get(url) as r:
                            if r.status == 200:
                                return await r.read()
                except Exception:
                    return None
            _prefetch_task = asyncio.create_task(_prefetch_first_frame(first_frame_url))
            logger.info(f"[{workflow_id}] Pre-fetching first frame in background")

        # Get video parameters from internal_config or legacy params
        logger.info(f"[{workflow_id}] Reading video parameters from config")
        # Try nested path first (stage4_video.generation.*)
        video_model = _get_config(req, "stage4_video", "model", None)
        if video_model is None and req.internal_config:
            video_model = req.internal_config.get("stage4_video", {}).get("generation", {}).get("model", "A14B")
        if video_model is None:
            video_model = "A14B"

        # Get model_preset
        video_model_preset = _get_config(req, "stage4_video", "model_preset", None)
        if video_model_preset is None and req.internal_config:
            video_model_preset = req.internal_config.get("stage4_video", {}).get("generation", {}).get("model_preset", "")
        if video_model_preset is None:
            video_model_preset = ""

        video_resolution = _get_config(req, "stage4_video", "resolution", None)
        if video_resolution is None and req.internal_config:
            video_resolution = req.internal_config.get("stage4_video", {}).get("generation", {}).get("resolution", "480p_3:4")
        if video_resolution is None:
            video_resolution = "480p_3:4"

        video_duration = _get_config(req, "stage4_video", "duration", None)
        if video_duration is None and req.internal_config:
            video_duration = req.internal_config.get("stage4_video", {}).get("generation", {}).get("duration", "5s")
        if video_duration is None:
            video_duration = "5s"

        video_steps = _get_config(req, "stage4_video", "steps", None)
        if video_steps is None and req.internal_config:
            video_steps = req.internal_config.get("stage4_video", {}).get("generation", {}).get("steps", 20)
        if video_steps is None:
            video_steps = 20

        video_cfg = _get_config(req, "stage4_video", "cfg", None)
        if video_cfg is None and req.internal_config:
            video_cfg = req.internal_config.get("stage4_video", {}).get("generation", {}).get("cfg", 6.0)
        if video_cfg is None:
            video_cfg = 6.0

        video_shift = _get_config(req, "stage4_video", "shift", None)
        if video_shift is None and req.internal_config:
            video_shift = req.internal_config.get("stage4_video", {}).get("generation", {}).get("shift", 5.0)
        if video_shift is None:
            video_shift = 5.0

        video_scheduler = _get_config(req, "stage4_video", "scheduler", None)
        if video_scheduler is None and req.internal_config:
            video_scheduler = req.internal_config.get("stage4_video", {}).get("generation", {}).get("scheduler", "unipc")
        if video_scheduler is None:
            video_scheduler = "unipc"

        video_noise_aug = _get_config(req, "stage4_video", "noise_aug_strength", None)
        if video_noise_aug is None and req.internal_config:
            video_noise_aug = req.internal_config.get("stage4_video", {}).get("generation", {}).get("noise_aug_strength", None)
        if video_noise_aug is None:
            # Default: 0.2 for I2V (helps with motion quality), 0.0 for T2V (no first frame)
            # Continuation is always I2V (uses parent's last frame), even if req.mode == "t2v"
            is_actually_i2v = is_continuation or req.mode != "t2v"
            video_noise_aug = 0.2 if is_actually_i2v else 0.0

        # Auto-boost noise_aug for prerequisite transitions (large motion from first frame)
        if analysis_result and analysis_result.get("has_prerequisite") and (req.mode != "t2v" or is_continuation):
            min_noise_aug = 0.2
            if float(video_noise_aug) < min_noise_aug:
                logger.info(f"[{workflow_id}] Boosting noise_aug_strength {video_noise_aug} -> {min_noise_aug} (prerequisite transition detected)")
                video_noise_aug = min_noise_aug

        video_motion_amp = _get_config(req, "stage4_video", "motion_amplitude", None)
        if video_motion_amp is None and req.internal_config:
            video_motion_amp = req.internal_config.get("stage4_video", {}).get("generation", {}).get("motion_amplitude", 1.15)
        if video_motion_amp is None:
            video_motion_amp = 1.15

        # T5 and CLIP presets
        t5_preset = _get_config(req, "stage4_video", "t5_preset", None)
        if t5_preset is None and req.internal_config:
            t5_preset = req.internal_config.get("stage4_video", {}).get("generation", {}).get("t5_preset", "nsfw")
        if t5_preset is None:
            t5_preset = "nsfw"

        clip_preset = _get_config(req, "stage4_video", "clip_preset", None)
        if clip_preset is None and req.internal_config:
            clip_preset = req.internal_config.get("stage4_video", {}).get("generation", {}).get("clip_preset", "nsfw")
        if clip_preset is None:
            clip_preset = "nsfw"

        # Continuation: inherit stage4 generation params from parent workflow
        # to ensure visual consistency (same model, cfg, steps, scheduler, etc.)
        if is_continuation and parent_workflow:
            try:
                parent_ic = json.loads(parent_workflow.get("internal_config", "{}"))
                parent_gen = parent_ic.get("stage4_video", {}).get("generation", {})
                if parent_gen:
                    _inherited = []
                    _param_map = {
                        "model": ("video_model", video_model),
                        "model_preset": ("video_model_preset", video_model_preset),
                        "steps": ("video_steps", video_steps),
                        "cfg": ("video_cfg", video_cfg),
                        "scheduler": ("video_scheduler", video_scheduler),
                        "shift": ("video_shift", video_shift),
                        "noise_aug_strength": ("video_noise_aug", video_noise_aug),
                        "motion_amplitude": ("video_motion_amp", video_motion_amp),
                    }
                    for param_key, (var_name, current_val) in _param_map.items():
                        if param_key in parent_gen:
                            parent_val = parent_gen[param_key]
                            if parent_val != current_val:
                                _inherited.append(f"{param_key}: {current_val} -> {parent_val}")
                    # Apply overrides
                    if "model" in parent_gen:
                        video_model = parent_gen["model"]
                    if "model_preset" in parent_gen:
                        video_model_preset = parent_gen["model_preset"]
                    if "steps" in parent_gen:
                        video_steps = parent_gen["steps"]
                    if "cfg" in parent_gen:
                        video_cfg = parent_gen["cfg"]
                    if "scheduler" in parent_gen:
                        video_scheduler = parent_gen["scheduler"]
                    if "shift" in parent_gen:
                        video_shift = parent_gen["shift"]
                    if "noise_aug_strength" in parent_gen:
                        video_noise_aug = parent_gen["noise_aug_strength"]
                    if "motion_amplitude" in parent_gen:
                        video_motion_amp = parent_gen["motion_amplitude"]
                    if _inherited:
                        logger.info(f"[{workflow_id}] Continuation: inherited stage4 params from parent: {', '.join(_inherited)}")
                    else:
                        logger.info(f"[{workflow_id}] Continuation: parent stage4 params match, no override needed")
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"[{workflow_id}] Continuation: failed to inherit parent stage4 params: {e}")

        # Continuation: ensure minimum noise_aug for I2V motion quality
        if is_continuation and float(video_noise_aug) < 0.2:
            logger.info(f"[{workflow_id}] Continuation: boosting noise_aug {video_noise_aug} -> 0.2 (I2V continuation minimum)")
            video_noise_aug = 0.2

        # Convert model string to ModelType enum
        if video_model.upper() == "A14B":
            model = ModelType.A14B
        elif video_model.upper() == "5B":
            model = ModelType.FIVE_B
        else:
            model = ModelType.A14B  # Default fallback
        resolution = video_resolution
        duration = video_duration
        steps = video_steps
        cfg = video_cfg

        # Parse resolution: auto-calculate from "<p>p_<w>_<h>" or "<p>p_<w>:<h>" pattern
        # "p" value = shorter side for landscape, longer side for portrait
        def _round16(v: float) -> int:
            return max(16, int(round(v / 16)) * 16)

        import re as _re
        _res_match = _re.match(r'^(\d+)p[_:]?(\d+)[_:](\d+)$', resolution.replace('p_', 'p_').replace('p:', 'p_'))
        if _res_match:
            p_val = int(_res_match.group(1))
            ar_w = int(_res_match.group(2))
            ar_h = int(_res_match.group(3))
            if ar_w >= ar_h:  # landscape or square: p = height
                height = _round16(p_val)
                width = _round16(p_val * ar_w / ar_h)
            else:  # portrait: p = width
                width = _round16(p_val)
                height = _round16(p_val * ar_h / ar_w)
        else:
            width, height = 832, 480
            logger.warning(f"[{workflow_id}] Unknown resolution '{resolution}', using default 832x480")
        logger.info(f"[{workflow_id}] Resolution '{resolution}' -> target {width}x{height}")

        # Continuation: override resolution with parent video's ACTUAL dimensions
        # Parent stores actual_width/actual_height after generation (not the frontend's resolution setting)
        if is_continuation and parent_workflow:
            parent_aw = parent_workflow.get("actual_width")
            parent_ah = parent_workflow.get("actual_height")
            if parent_aw and parent_ah:
                width = int(parent_aw)
                height = int(parent_ah)
                logger.info(f"[{workflow_id}] Continuation: inherited actual dimensions from parent -> {width}x{height}")
            else:
                # Fallback: parse parent's resolution string (legacy workflows without actual_width/height)
                try:
                    parent_ic = json.loads(parent_workflow.get("internal_config", "{}"))
                    parent_res = parent_ic.get("stage4_video", {}).get("generation", {}).get("resolution", "")
                    if parent_res:
                        _parent_match = _re.match(r'^(\d+)p[_:]?(\d+)[_:](\d+)$', parent_res.replace('p_', 'p_').replace('p:', 'p_'))
                        if _parent_match:
                            p_val = int(_parent_match.group(1))
                            ar_w = int(_parent_match.group(2))
                            ar_h = int(_parent_match.group(3))
                            if ar_w >= ar_h:
                                height = _round16(p_val)
                                width = _round16(p_val * ar_w / ar_h)
                            else:
                                width = _round16(p_val)
                                height = _round16(p_val * ar_h / ar_w)
                            logger.info(f"[{workflow_id}] Continuation: inherited resolution from parent config '{parent_res}' -> {width}x{height}")
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"[{workflow_id}] Continuation: failed to inherit parent resolution: {e}")

        # Parse duration
        duration_seconds = float(duration.rstrip('s'))

        # Determine image mode based on workflow mode (MUST be before LoRA filtering)
        # Continuation MUST use FIRST_FRAME (I2V) to ensure visual continuity from parent's last frame
        if is_continuation:
            image_mode = ImageMode.FIRST_FRAME
            logger.info(f"[{workflow_id}] Continuation: forced image_mode=FIRST_FRAME for visual continuity")
        elif req.mode == "face_reference":
            image_mode = ImageMode.FACE_REFERENCE
        elif req.mode == "full_body_reference":
            image_mode = ImageMode.FULL_BODY_REFERENCE
        else:
            # Covers first_frame and t2v (t2v with pose becomes internal I2V, t2v without pose is pure T2V)
            image_mode = ImageMode.FIRST_FRAME

        # Use pre-built video prompt from Stage 1 analysis
        prompt = req.user_prompt
        auto_prompt = _get_config(req, "stage1_prompt_analysis", "auto_prompt", True)
        if analysis_result and auto_prompt:
            video_prompt = analysis_result.get("video_prompt") or analysis_result.get("optimized_i2v_prompt")
            if video_prompt:
                prompt = video_prompt

        # Use pre-selected video LoRAs from Stage 1 analysis (already includes defaults)
        loras = []
        auto_lora = _get_config(req, "stage1_prompt_analysis", "auto_lora", True)
        # Stand-In has its own LoRA injection — skip auto LoRA to avoid conflicts
        standin_for_lora = _get_config(req, "stage4_video", "standin_enabled", False)
        if standin_for_lora:
            auto_lora = False
            logger.info(f"[{workflow_id}] Stand-In mode: disabled auto_lora (Stand-In injects its own LoRAs)")
        if analysis_result and auto_lora:
            video_loras = analysis_result.get("video_loras", [])

            # Determine if we're in I2V or T2V mode for LoRA filtering
            is_i2v_mode = image_mode in [ImageMode.FIRST_FRAME, ImageMode.FACE_REFERENCE, ImageMode.FULL_BODY_REFERENCE]

            # Filter LoRAs by mode compatibility
            filtered_loras = []
            for lora in video_loras:
                lora_mode = lora.get("mode", "").upper()
                lora_noise = lora.get("noise_stage") or ""
                if is_i2v_mode:
                    if lora_mode == "I2V" or lora_noise in ("high", "low", "single") or not lora_mode:
                        filtered_loras.append(lora)
                else:
                    if lora_mode == "T2V" or not lora_mode:
                        filtered_loras.append(lora)

            if not filtered_loras:
                filtered_loras = video_loras

            # Read trigger injection switches
            inject_trigger_prompt = _get_config(req, "stage1_prompt_analysis", "inject_trigger_prompt", True)
            inject_trigger_words = _get_config(req, "stage1_prompt_analysis", "inject_trigger_words", True)

            selected_loras = filtered_loras[:3]

            # Normalize LoRA weights: cap total weight sum to prevent overfitting
            MAX_TOTAL_LORA_WEIGHT = 1.0
            total_weight = sum(l.get("weight", 0.8) for l in selected_loras)
            if total_weight > MAX_TOTAL_LORA_WEIGHT and total_weight > 0:
                scale = MAX_TOTAL_LORA_WEIGHT / total_weight
                logger.info(f"[{workflow_id}] LoRA weight normalization: total {total_weight:.2f} > {MAX_TOTAL_LORA_WEIGHT}, scaling by {scale:.2f}")
                for l in selected_loras:
                    l["weight"] = round(l.get("weight", 0.8) * scale, 2)

            for lora in selected_loras:
                tw = lora.get("trigger_words", [])
                if isinstance(tw, str):
                    import json as _json
                    try:
                        tw = _json.loads(tw)
                    except Exception:
                        tw = []
                loras.append(LoraInput(
                    name=lora["name"],
                    strength=lora.get("weight", 0.8),
                    trigger_words=tw if inject_trigger_words else [],
                    trigger_prompt=lora.get("trigger_prompt") if inject_trigger_prompt else None,
                ))
                logger.info(f"[{workflow_id}] Video LoRA: {lora['name']} (weight={lora.get('weight')}, noise_stage={lora.get('noise_stage')})")

        # Download first frame for upload (use prefetched data if available)
        image_file = None
        if first_frame_url:
            # Handle both URL and local filename
            if first_frame_url.startswith('http://') or first_frame_url.startswith('https://'):
                # Try to use prefetched data from parallel download
                image_data = None
                if _prefetch_task is not None:
                    try:
                        image_data = await _prefetch_task
                        if image_data:
                            logger.info(f"[{workflow_id}] Using pre-fetched first frame ({len(image_data)} bytes)")
                    except Exception as e:
                        logger.warning(f"[{workflow_id}] Pre-fetch failed, downloading normally: {e}")
                        image_data = None

                if not image_data:
                    # Fallback: download normally
                    from api.services.video_frame_extractor import convert_video_url_to_frame
                    converted_url = await convert_video_url_to_frame(first_frame_url)
                    if converted_url != first_frame_url:
                        logger.info(f"[{workflow_id}] Converted video URL to frame: {first_frame_url} -> {converted_url}")
                        first_frame_url = converted_url
                    async with aiohttp.ClientSession() as session:
                        async with session.get(first_frame_url) as resp:
                            if resp.status != 200:
                                raise Exception(f"Failed to download first frame: {resp.status}")
                            image_data = await resp.read()
            elif first_frame_url.startswith('/api/v1/results/'):
                # Results path - extract filename and read from RESULTS_DIR
                from api.config import RESULTS_DIR
                filename = first_frame_url.split('/')[-1]
                local_path = RESULTS_DIR / filename
                if not local_path.exists():
                    raise Exception(f"First frame file not found in results: {first_frame_url}")
                image_data = local_path.read_bytes()
                logger.info(f"[{workflow_id}] Read first frame from results: {local_path}")
            elif first_frame_url.startswith('/api/v1/uploads/'):
                # API uploads path - extract filename and read from UPLOADS_DIR
                filename = first_frame_url.split('/')[-1]
                local_path = UPLOADS_DIR / filename
                if not local_path.exists():
                    raise Exception(f"First frame file not found in uploads: {first_frame_url}")
                image_data = local_path.read_bytes()
                logger.info(f"[{workflow_id}] Read first frame from API uploads: {local_path}")
            elif first_frame_url.startswith('/uploads/'):
                # Uploads path - extract filename and read from UPLOADS_DIR
                filename = first_frame_url.split('/')[-1]
                local_path = UPLOADS_DIR / filename
                if not local_path.exists():
                    raise Exception(f"First frame file not found in uploads: {first_frame_url}")
                image_data = local_path.read_bytes()
                logger.info(f"[{workflow_id}] Read first frame from uploads: {local_path}")
            elif first_frame_url.startswith('/pose-files/'):
                # Pose reference files - /pose-files/<pose>/<filename> -> data/pose_references/<pose>/<filename>
                from api.routes.pose_images import POSE_DIR
                rel_path = first_frame_url[len('/pose-files/'):]
                local_path = POSE_DIR / rel_path
                if not local_path.exists():
                    raise Exception(f"Pose file not found: {local_path}")
                image_data = local_path.read_bytes()
                logger.info(f"[{workflow_id}] Read first frame from pose files: {local_path}")
            else:
                # Plain filename - try UPLOADS_DIR first, then RESULTS_DIR
                from api.config import RESULTS_DIR
                filename = first_frame_url.split('/')[-1]
                local_path = UPLOADS_DIR / filename
                if not local_path.exists():
                    local_path = RESULTS_DIR / filename
                if not local_path.exists():
                    raise Exception(f"First frame file not found: {first_frame_url}")
                image_data = local_path.read_bytes()
                logger.info(f"[{workflow_id}] Read first frame: {local_path}")

            # Create UploadFile object
            image_file = UploadFile(
                filename="first_frame.png",
                file=BytesIO(image_data)
            )
        else:
            logger.info(f"[{workflow_id}] No first frame provided, chain will use T2V mode")

        # Prepare face_image if reference_image is provided (for face_reference mode)
        face_image_file = None
        if req.reference_image and image_mode == ImageMode.FACE_REFERENCE:
            # Load reference image
            if req.reference_image.startswith('data:image'):
                face_b64 = req.reference_image.split(',')[1]
                face_data = base64.b64decode(face_b64)
            elif req.reference_image.startswith('http://') or req.reference_image.startswith('https://'):
                async with aiohttp.ClientSession() as session:
                    async with session.get(req.reference_image) as resp:
                        if resp.status == 200:
                            face_data = await resp.read()
                        else:
                            logger.warning(f"[{workflow_id}] Failed to download reference image: {resp.status}")
                            face_data = None
            elif req.reference_image.startswith('/uploads/'):
                filename = req.reference_image.split('/')[-1]
                local_path = UPLOADS_DIR / filename
                if local_path.exists():
                    face_data = local_path.read_bytes()
                else:
                    logger.warning(f"[{workflow_id}] Reference image not found: {req.reference_image}")
                    face_data = None
            elif '/' not in req.reference_image and '.' in req.reference_image:
                local_path = UPLOADS_DIR / req.reference_image
                if local_path.exists():
                    face_data = local_path.read_bytes()
                else:
                    logger.warning(f"[{workflow_id}] Reference image not found: {req.reference_image}")
                    face_data = None
            else:
                logger.warning(f"[{workflow_id}] Unsupported reference_image format: {req.reference_image}")
                face_data = None

            if face_data:
                face_image_file = UploadFile(
                    filename="reference_face.png",
                    file=BytesIO(face_data)
                )
                logger.info(f"[{workflow_id}] Prepared face_image for face_reference mode")

        # Stand-In: upload face reference image to ComfyUI if enabled
        standin_face_comfy_name = None
        standin_enabled = _get_config(req, "stage4_video", "standin_enabled", False)
        if standin_enabled and req.mode == "t2v" and req.reference_image:
            try:
                standin_face_data = None
                if req.reference_image.startswith('data:image'):
                    import base64 as b64mod
                    b64_data = req.reference_image.split(",", 1)[1] if "," in req.reference_image else req.reference_image
                    standin_face_data = b64mod.b64decode(b64_data)
                elif req.reference_image.startswith('http'):
                    async with aiohttp.ClientSession() as session:
                        async with session.get(req.reference_image) as resp:
                            if resp.status == 200:
                                standin_face_data = await resp.read()
                else:
                    from api.config import UPLOADS_DIR, RESULTS_DIR
                    filename = req.reference_image.split('/')[-1]
                    for d in [UPLOADS_DIR, RESULTS_DIR]:
                        local_path = d / filename
                        if local_path.exists():
                            standin_face_data = local_path.read_bytes()
                            break

                if standin_face_data:
                    client = task_manager._get_client(model.value)
                    upload_result = await client.upload_image(standin_face_data, "standin_face.png")
                    standin_face_comfy_name = upload_result.get("name", "standin_face.png")
                    logger.info(f"[{workflow_id}] Stand-In: uploaded face reference as '{standin_face_comfy_name}'")
            except Exception as e:
                logger.warning(f"[{workflow_id}] Stand-In: failed to upload face reference: {e}")

        # Build chain request
        chain_req = AutoChainRequest(
            prompt=prompt,
            model=model,
            model_preset=video_model_preset,
            width=width,
            height=height,
            duration=duration_seconds,
            fps=16,  # Default FPS for optimal performance
            steps=steps,
            cfg=cfg,
            shift=video_shift,
            scheduler=video_scheduler,
            loras=loras,
            image_mode=image_mode,
            auto_continue=False,
            noise_aug_strength=video_noise_aug,
            motion_amplitude=video_motion_amp,
            t5_preset=t5_preset,
            clip_preset=clip_preset,
            standin_face_image=standin_face_comfy_name,
            segments=[ChainSegment(
                prompt=prompt,
                duration=duration_seconds,
                loras=[]
            )]
        )

        # Story continuation: set parent video/chain references
        if is_continuation and not parent_workflow:
            logger.warning(f"[{workflow_id}] is_continuation=True but parent_workflow is None, Story Mode skipped")
        if is_continuation and parent_workflow:
            parent_video_url = parent_workflow.get("final_video_url")
            if parent_video_url:
                chain_req.parent_video_url = parent_video_url
                logger.info(f"[{workflow_id}] Story continuation: parent_video_url={parent_video_url}")
            parent_chain_id = parent_workflow.get("chain_id")
            if parent_chain_id:
                chain_req.parent_chain_id = parent_chain_id
                logger.info(f"[{workflow_id}] Story continuation: parent_chain_id={parent_chain_id}")

            # Set origin first frame as CLIP Vision identity anchor
            if origin_first_frame_url:
                chain_req.initial_reference_url = origin_first_frame_url
                logger.info(f"[{workflow_id}] Story continuation: initial_reference_url={origin_first_frame_url}")

        # Log video generation parameters
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - Video generation parameters:")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - model: {video_model} -> {model}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - resolution: {video_resolution} -> {width}x{height}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - duration: {video_duration} -> {duration_seconds}s")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - fps: 16")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - num_frames: {int(duration_seconds * 16) + 1}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - steps: {video_steps}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - cfg: {video_cfg}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - shift: {video_shift}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - scheduler: {video_scheduler}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - noise_aug_strength: {video_noise_aug}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - motion_amplitude: {video_motion_amp}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - t5_preset: {t5_preset}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - clip_preset: {clip_preset}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - image_mode: {image_mode}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - prompt: {prompt}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - loras: {[f'{l.name}:{l.strength}' for l in loras]}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - first_frame_url: {first_frame_url}")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - model_preset: {video_model_preset}")

        # Apply postprocess configuration
        postprocess_config = _get_config(req, "stage4_video", "postprocess", {})
        if isinstance(postprocess_config, dict):
            # Upscale
            upscale_config = postprocess_config.get("upscale", {})
            if upscale_config.get("enabled"):
                chain_req.enable_upscale = True
                raw_model = upscale_config.get("model", "4x_foolhardy_Remacri")
                # Map legacy model names to supported models
                _upscale_model_map = {
                    "4x-UltraSharp": "4x_NMKD-Siax_200k",
                    "RealESRGAN_x4plus": "4x_NMKD-Siax_200k",
                    "RealESRGAN_x2plus": "RealESRGAN_x2plus.pth",  # PyTorch path
                    "realesrgan-x4plus": "4x_NMKD-Siax_200k",
                }
                chain_req.upscale_model = _upscale_model_map.get(raw_model, raw_model)
                if chain_req.upscale_model != raw_model:
                    logger.warning(f"[VIDEO_PARAMS] {workflow_id} - upscale model '{raw_model}' remapped to '{chain_req.upscale_model}'")
                # Read resize factor from config (supports float like 2.0 or string like "2x")
                raw_resize = upscale_config.get("resize", 1.5)
                if isinstance(raw_resize, str):
                    resize_factor = float(raw_resize.lower().rstrip('x'))
                else:
                    resize_factor = float(raw_resize)
                # Snap to 0.5 steps (TRT supports 1x, 1.5x, 2x, 2.5x, ...)
                resize_factor = round(resize_factor * 2) / 2
                if resize_factor < 1.0:
                    resize_factor = 1.5
                chain_req.upscale_resize = f"{int(resize_factor)}x" if resize_factor == int(resize_factor) else f"{resize_factor}x"
                gen_width = max(16, int(round(width / resize_factor / 16)) * 16)
                gen_height = max(16, int(round(height / resize_factor / 16)) * 16)
                # Ensure minimum generation size (avoid too-small gen dims, e.g. 480p+2x=240p)
                MIN_GEN_DIM = 320
                if gen_width < MIN_GEN_DIM or gen_height < MIN_GEN_DIM:
                    # Clamp resize_factor so neither dimension falls below minimum
                    max_factor_w = width / MIN_GEN_DIM
                    max_factor_h = height / MIN_GEN_DIM
                    resize_factor = min(resize_factor, max_factor_w, max_factor_h)
                    resize_factor = max(1.0, round(resize_factor * 2) / 2)  # round to 0.5 steps (TRT supports 1x,1.5x,2x,...)
                    if resize_factor <= 1.0:
                        # 1.0x means no actual upscaling — disable to avoid UpscalerTensorrt validation failure
                        chain_req.enable_upscale = False
                        logger.warning(f"[VIDEO_PARAMS] {workflow_id} - upscale disabled: resize_factor clamped to 1.0x (gen dims already at minimum {MIN_GEN_DIM})")
                    else:
                        chain_req.upscale_resize = f"{int(resize_factor)}x" if resize_factor == int(resize_factor) else f"{resize_factor}x"
                        gen_width = max(MIN_GEN_DIM, int(round(width / resize_factor / 16)) * 16)
                        gen_height = max(MIN_GEN_DIM, int(round(height / resize_factor / 16)) * 16)
                        logger.warning(f"[VIDEO_PARAMS] {workflow_id} - resize_factor clamped to {resize_factor}x to keep gen dims >= {MIN_GEN_DIM}")
                if chain_req.enable_upscale:
                    chain_req.width = gen_width
                    chain_req.height = gen_height
                    logger.info(f"[VIDEO_PARAMS] {workflow_id} - upscale enabled: model={chain_req.upscale_model}, resize={chain_req.upscale_resize}, gen={gen_width}x{gen_height} -> target={width}x{height}")
                else:
                    chain_req.width = width
                    chain_req.height = height

            # Interpolation
            interp_config = postprocess_config.get("interpolation", {})
            if interp_config.get("enabled"):
                chain_req.enable_interpolation = True
                chain_req.interpolation_multiplier = interp_config.get("multiplier", 2)
                chain_req.interpolation_profile = interp_config.get("profile", "auto")
                logger.info(f"[VIDEO_PARAMS] {workflow_id} - interpolation enabled: multiplier={chain_req.interpolation_multiplier}, profile={chain_req.interpolation_profile}")

            # MMAudio
            mmaudio_config = postprocess_config.get("mmaudio", {})
            if mmaudio_config.get("enabled"):
                chain_req.enable_mmaudio = True
                chain_req.mmaudio_prompt = mmaudio_config.get("prompt", "")
                chain_req.mmaudio_negative_prompt = mmaudio_config.get("negative_prompt", "")
                chain_req.mmaudio_steps = mmaudio_config.get("steps", 12)
                chain_req.mmaudio_cfg = mmaudio_config.get("cfg", 4.5)
                logger.info(f"[VIDEO_PARAMS] {workflow_id} - mmaudio enabled: steps={chain_req.mmaudio_steps}, cfg={chain_req.mmaudio_cfg}, prompt='{chain_req.mmaudio_prompt[:50]}'")

        # Store actual generation dimensions for continuation inheritance
        # (frontend's resolution setting may differ from what's actually used)
        await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
            "actual_width": str(chain_req.width),
            "actual_height": str(chain_req.height),
        })

        # Call chain generation endpoint
        params_json = chain_req.model_dump_json()
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - Final chain_req parameters:")
        logger.info(f"[VIDEO_PARAMS] {workflow_id} - {params_json}")
        logger.info(f"[{workflow_id}] Calling generate_chain with image_mode={chain_req.image_mode}")
        result = await generate_chain(
            image=image_file,
            face_image=face_image_file,
            initial_reference_image=None,
            params=params_json,
            _=None
        )

        # Poll for completion
        chain_id = result.chain_id
        logger.info(f"[{workflow_id}] Chain created: {chain_id}")
        # Save chain_id immediately so workflow status can track Stage 4 progress
        await task_manager.redis.hset(f"workflow:{workflow_id}", "chain_id", chain_id)
        final_video_url = None
        status = "unknown"

        for _ in range(180):  # Poll for up to 15 minutes
            await asyncio.sleep(5)

            chain_data = await task_manager.redis.hgetall(f"chain:{chain_id}")
            status = chain_data.get("status", "unknown")

            if status == "completed":
                final_video_url = chain_data.get("final_video_url")
                logger.info(f"[{workflow_id}] Chain completed, video URL: {final_video_url}")
                break
            elif status == "failed":
                error = chain_data.get("error", "Unknown error")
                logger.error(f"[{workflow_id}] Chain failed: {error}")
                raise Exception(f"Chain generation failed: {error}")
            elif status == "partial":
                # Some segments completed but chain failed — extract partial video if any
                error = chain_data.get("error", "Unknown error")
                final_video_url = chain_data.get("final_video_url")
                logger.warning(f"[{workflow_id}] Chain partial: {error}, video: {final_video_url}")
                break

        if not final_video_url:
            raise TimeoutError(f"[{workflow_id}] Chain {chain_id} polling timeout after 15min, last status: {status}")

        # Return chain_id, final_video_url, and loras list (with trigger_words for display)
        loras_info = [{"name": l.name, "strength": l.strength, "trigger_words": l.trigger_words, "trigger_prompt": l.trigger_prompt} for l in loras]
        return chain_id, final_video_url, loras_info

    except Exception as e:
        logger.error(f"[{workflow_id}] Video generation failed: {e}", exc_info=True)
        raise
