"""
Advanced workflow execution logic.

This module contains the async orchestration logic for the advanced workflow system.
"""
import logging
import base64
import json
from typing import Optional, Any
import aiohttp
from api.config import UPLOADS_DIR

logger = logging.getLogger(__name__)


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


async def _execute_workflow(workflow_id: str, req, task_manager):
    """
    Execute the complete advanced workflow asynchronously.

    This function orchestrates:
    1. Prompt analysis and LORA recommendation
    2. First frame acquisition (upload/generate/select)
    3. SeeDream editing
    4. Video generation via Chain workflow
    """
    try:
        # =============================================
        # Story Continuation: load parent workflow data
        # =============================================
        is_continuation = bool(getattr(req, 'parent_workflow_id', None))
        parent_workflow = None

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

            # Auto-inherit reference_image from parent if not provided
            if not req.reference_image and parent_workflow.get("reference_image"):
                req.reference_image = parent_workflow["reference_image"]
                logger.info(f"[{workflow_id}] Inherited reference_image from parent")

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
        await _update_stage(task_manager, workflow_id, "prompt_analysis", "running")

        analysis_result = None
        auto_analyze = _get_config(req, "stage1_prompt_analysis", "auto_analyze", True)

        if is_continuation:
            # Story continuation: run full analysis (LoRA + prompt) for the NEW prompt
            # Do NOT inherit LoRAs from parent — the continuation may need different LoRAs
            if auto_analyze:
                analysis_result = await _analyze_prompt(req, task_manager)
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
            logger.info(f"[{workflow_id}] Continuation Stage 1: full analysis for new prompt")

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

        if is_continuation:
            # Extract last frame from parent's final video as continuation start frame
            await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "running", details_dict={
                "source": "parent_video_last_frame",
                "parent_workflow_id": req.parent_workflow_id,
            })

            parent_video_url = parent_workflow.get("final_video_url")
            if not parent_video_url:
                raise Exception("Parent workflow has no final video for continuation")

            from api.services.ffmpeg_utils import extract_last_frame
            from api.services import storage

            video_path = await storage.get_video_path_from_url(parent_video_url)
            if not video_path or not video_path.exists():
                raise Exception(f"Parent video file not found: {parent_video_url}")

            last_frame_path = await extract_last_frame(video_path)
            frame_data = last_frame_path.read_bytes()
            _, first_frame_url = await storage.save_upload(frame_data, last_frame_path.name)

            await task_manager.redis.hset(f"workflow:{workflow_id}", "first_frame_url", first_frame_url)
            await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "completed", details_dict={
                "source": "parent_video_last_frame",
                "parent_workflow_id": req.parent_workflow_id,
                "parent_video_url": parent_video_url,
                "url": first_frame_url,
            })
            logger.info(f"[{workflow_id}] Continuation Stage 2: extracted last frame from parent video: {first_frame_url}")
        else:
            await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "running")

            first_frame_url = await _acquire_first_frame(workflow_id, req, analysis_result, task_manager)
            if first_frame_url:
                await task_manager.redis.hset(f"workflow:{workflow_id}", "first_frame_url", first_frame_url)

            # 保存结构化详细信息
            if req.mode == "first_frame":
                if req.uploaded_first_frame:
                    source_text = "upload"
                elif first_frame_url and analysis_result and analysis_result.get("reference_image"):
                    source_text = "pose_reference"
                elif first_frame_url is None:
                    source_text = "t2v_fallback"
                else:
                    source_text = req.first_frame_source or "unknown"
            else:
                first_frame_source_for_display = req.first_frame_source or _get_config(req, "stage2_first_frame", "first_frame_source", "select_existing")
                source_text = first_frame_source_for_display

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
                        # 正常执行 Reactor
                        logger.info(f"[{workflow_id}] Applying face swap to first frame: {first_frame_url}")
                        swapped_url = await _apply_face_swap_to_frame(
                            first_frame_url,
                            req.reference_image,
                            strength=face_swap_config.get("strength", 1.0),
                            task_manager=task_manager
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

        if is_continuation:
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

            if req.mode == "first_frame":
                # first_frame 模式跳过 SeeDream
                should_run_seedream = False
                skip_reason = "跳过（首帧模式）"

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

                    # 使用视频目标分辨率作为 SeeDream 分辨率（无需比视频更大）
                    import re as _re
                    res_str = req.resolution or "480p"
                    _m = _re.match(r'(\d+)', res_str)
                    p_val = int(_m.group(1)) if _m else 480
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
                        # skip_reactor 图片 + SeeDream 失败 → Reactor 补救
                        logger.info(f"[{workflow_id}] SeeDream failed, applying deferred reactor fallback")
                        swapped_url = await _apply_face_swap_to_frame(
                            first_frame_url,
                            req.reference_image,
                            strength=face_swap_config.get("strength", 1.0),
                            task_manager=task_manager
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
        if analysis_result and auto_prompt:
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

        chain_id, final_video_url, loras_info = await _generate_video(workflow_id, req, edited_frame_url, analysis_result, task_manager, is_continuation=is_continuation, parent_workflow=parent_workflow)

        if chain_id:
            await task_manager.redis.hset(f"workflow:{workflow_id}", "chain_id", chain_id)
        if final_video_url:
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
        await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
            "status": "completed",
            "completed_at": str(int(_time.time()))
        })
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
    mapping = {f"stage_{stage_name}": status}
    if error:
        mapping[f"stage_{stage_name}_error"] = error
    if details_dict:
        # Use structured dict (preferred)
        mapping[f"stage_{stage_name}_details"] = json.dumps(details_dict, ensure_ascii=False)
    elif details:
        # Legacy text details - save as-is for backward compatibility
        mapping[f"stage_{stage_name}_details"] = details
    await task_manager.redis.hset(f"workflow:{workflow_id}", mapping=mapping)


async def _analyze_prompt(req, task_manager) -> Optional[dict]:
    """Call /workflow/analyze or /poses/recommend-workflow endpoint internally"""
    try:
        auto_prompt = _get_config(req, "stage1_prompt_analysis", "auto_prompt", True)
        skip_llm = not auto_prompt  # turbo mode: skip LLM prompt optimization and reranking

        # Auto-recommend poses if not provided
        pose_keys = req.pose_keys
        if not pose_keys:
            from api.routes.poses import recommend_poses_by_prompt, PoseRecommendRequest
            try:
                pose_req = PoseRecommendRequest(prompt=req.user_prompt, top_k=5, use_llm=not skip_llm)
                pose_result = await recommend_poses_by_prompt(pose_req, _=None)
                if pose_result.recommendations:
                    # Select the first (highest similarity) pose
                    selected_pose = pose_result.recommendations[0]
                    pose_keys = [selected_pose.pose_key]
                    logger.info(f"Auto-selected pose: {selected_pose.pose_key} (score: {selected_pose.score:.3f}, llm_rerank={not skip_llm})")
            except Exception as e:
                logger.warning(f"Auto pose recommendation failed: {e}")

        # If pose_keys provided or auto-selected, use pose-based recommendation
        if pose_keys:
            from api.routes.poses import recommend_workflow, WorkflowRecommendRequest

            recommend_req = WorkflowRecommendRequest(
                prompt=req.user_prompt,
                pose_keys=pose_keys,
                skip_prompt_optimization=skip_llm
            )

            result = await recommend_workflow(recommend_req, _=None)

            # Convert to analysis_result format
            image_loras = [{"lora_id": l.lora_id, "name": l.lora_name, "weight": l.weight, "trigger_words": l.trigger_words, "trigger_prompt": l.trigger_prompt} for l in result.image_loras]
            video_loras = [{"lora_id": l.lora_id, "name": l.lora_name, "weight": l.weight, "trigger_words": l.trigger_words, "trigger_prompt": l.trigger_prompt, "noise_stage": l.noise_stage} for l in result.video_loras]

            # Fetch preview_url from MySQL lora_metadata
            all_lora_ids = [l["lora_id"] for l in image_loras + video_loras if l.get("lora_id")]
            if all_lora_ids:
                try:
                    import pymysql
                    from api.routes.recommend import DB_CONFIG
                    conn = pymysql.connect(**DB_CONFIG)
                    try:
                        cursor = conn.cursor(pymysql.cursors.DictCursor)
                        placeholders = ','.join(['%s'] * len(all_lora_ids))
                        cursor.execute(f"SELECT id, preview_url FROM lora_metadata WHERE id IN ({placeholders})", all_lora_ids)
                        preview_map = {row['id']: row['preview_url'] for row in cursor.fetchall()}
                        cursor.close()
                    finally:
                        conn.close()
                    for l in image_loras + video_loras:
                        l['preview_url'] = preview_map.get(l['lora_id'])
                except Exception as e:
                    logger.warning(f"Failed to fetch lora preview_urls: {e}")

            return {
                "optimized_prompt": result.optimized_prompt,
                "optimized_i2v_prompt": result.video_prompt,
                "optimized_t2i_prompt": result.image_prompt,
                "reference_image": result.reference_image,
                "reference_skip_reactor": result.reference_skip_reactor,
                "image_loras": image_loras,
                "video_loras": video_loras,
                "images": [{"url": result.reference_image, "skip_reactor": result.reference_skip_reactor}] if result.reference_image else [],
                "pose_keys": pose_keys
            }

        # No pose matched — do prompt optimization only, no LoRA/reference injection
        optimized_prompt = req.user_prompt
        if not skip_llm:
            try:
                from api.services.prompt_optimizer import PromptOptimizer
                optimizer = PromptOptimizer()
                result = await optimizer.optimize(
                    prompt=req.user_prompt,
                    trigger_words=[],
                    mode="i2v",
                    duration=5.0,
                )
                optimized_prompt = result["optimized_prompt"]
            except Exception as e:
                logger.warning(f"Prompt optimization failed: {e}")

        return {
            "optimized_prompt": optimized_prompt,
            "optimized_i2v_prompt": optimized_prompt,
            "optimized_t2i_prompt": req.user_prompt,
            "reference_image": None,
            "reference_skip_reactor": False,
            "image_loras": [],
            "video_loras": [],
            "images": [],
            "pose_keys": [],
        }

    except Exception as e:
        logger.error(f"Prompt analysis failed: {e}", exc_info=True)
        return {"_error": str(e)}


async def _acquire_first_frame(workflow_id: str, req, analysis_result: Optional[dict], task_manager) -> Optional[str]:
    """
    Acquire first frame based on first_frame_source.

    Returns: URL of the first frame image
    """
    try:
        from api.services import storage
        import uuid

        # Import video frame extractor
        from api.services.video_frame_extractor import convert_video_url_to_frame

        # Determine first_frame_source based on mode
        # In first_frame mode with upload, use uploaded image directly
        # Without upload, fall back to first_frame_source from config
        first_frame_source = req.first_frame_source or _get_config(req, "stage2_first_frame", "first_frame_source", "select_existing")
        logger.info(f"[{workflow_id}] Mode is {req.mode}, first_frame_source: {first_frame_source}")

        if req.mode == "first_frame" and req.uploaded_first_frame:

            # Handle different input formats
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
                    # Was a video, convert_video_url_to_frame returned a frame URL
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
                # Already uploaded, return as-is (relative URL path)
                logger.info(f"[{workflow_id}] Using already uploaded image: {req.uploaded_first_frame}")
                return req.uploaded_first_frame
            elif req.uploaded_first_frame.startswith('/uploads/'):
                # Short uploads path - convert to /api/v1/uploads/
                converted_path = '/api/v1' + req.uploaded_first_frame
                logger.info(f"[{workflow_id}] Converting {req.uploaded_first_frame} to {converted_path}")
                return converted_path
            elif '/' not in req.uploaded_first_frame and '.' in req.uploaded_first_frame:
                # Local filename (e.g., "abc123.png") - assume it's in uploads directory
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

        elif first_frame_source == "generate":
            # Use pose reference image if available; otherwise return None for T2V fallback
            if analysis_result and analysis_result.get("reference_image"):
                pose_url = analysis_result["reference_image"]
                logger.info(f"[{workflow_id}] Using pose reference image as first frame: {pose_url}")
                return pose_url
            else:
                logger.info(f"[{workflow_id}] No pose reference image, returning None for T2V fallback")
                return None

        elif first_frame_source == "select_existing":
            # Auto-select from recommended images
            logger.info(f"[{workflow_id}] Auto-selecting from recommended images")

            # Call recommend API to get recommended images
            if analysis_result and analysis_result.get("images"):
                # Use images from analysis_result if available
                recommended_images = analysis_result.get("images", [])
                logger.info(f"[{workflow_id}] Using {len(recommended_images)} images from analysis_result")
            else:
                # Call recommend API with lower threshold
                logger.info(f"[{workflow_id}] No images in analysis_result, calling recommend API")
                from api.routes.recommend import smart_recommend, RecommendRequest
                recommend_req = RecommendRequest(
                    prompt=req.user_prompt,
                    mode=req.mode,
                    include_images=True,
                    include_loras=False,
                    top_k_images=5,
                    min_similarity=0.3  # Lower threshold to get more results
                )
                recommend_result = await smart_recommend(recommend_req, _=None)
                recommended_images = [img.model_dump() for img in recommend_result.images]
                logger.info(f"[{workflow_id}] Recommend API returned {len(recommended_images)} images")

            if not recommended_images:
                logger.warning(f"[{workflow_id}] No recommended images found for select_existing mode, falling back to T2I generation")
                # Fallback to T2I generation
                return await _generate_t2i_image(req, analysis_result, task_manager)

            # Randomly select from all results
            import random
            selected_image = random.choice(recommended_images)
            selected_url = selected_image.get("url")
            logger.info(f"[{workflow_id}] Randomly selected resource: {selected_url} (similarity: {selected_image.get('similarity', 0.0):.3f})")

            # IMPORTANT: Use high resolution (1080p) for first frame extraction
            # The frame will be used for face swap and SeeDream editing, which need high quality
            # Video generation will resize to user's target resolution later
            base_res = {'width': 1920, 'height': 1080}  # Always use 1080p for image processing

            # Parse aspect ratio - handle missing aspect_ratio by deriving from resolution
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
                    # Default to 3:4 for portrait videos
                    ar_width, ar_height = 3, 4
                logger.info(f"[{workflow_id}] aspect_ratio not provided, derived {ar_width}:{ar_height} from resolution {video_resolution}")

            # Calculate actual dimensions based on aspect ratio (maintaining 1080p quality)
            if ar_width / ar_height > base_res['width'] / base_res['height']:
                width = base_res['width']
                height = round(base_res['width'] * ar_height / ar_width)
            else:
                height = base_res['height']
                width = round(base_res['height'] * ar_width / ar_height)

            # Ensure dimensions are multiples of 8
            width = round(width / 8) * 8
            height = round(height / 8) * 8

            logger.info(f"[{workflow_id}] Extracting frame at high resolution: {width}x{height} (aspect ratio {req.aspect_ratio})")

            # Convert video to first frame if needed
            selected_url = await convert_video_url_to_frame(selected_url, width, height)

            return selected_url

        else:
            raise Exception(f"Unknown first_frame_source: {first_frame_source}")

    except Exception as e:
        logger.error(f"First frame acquisition failed: {e}", exc_info=True)
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

        # Build prompt
        prompt = req.user_prompt
        if analysis_result and req.auto_prompt:
            optimized_t2i = analysis_result.get("optimized_t2i_prompt")
            if optimized_t2i:
                prompt = optimized_t2i

        # Add Image LoRAs to prompt
        auto_lora = _get_config(req, "stage1_prompt_analysis", "auto_lora", True)
        if analysis_result and auto_lora:
            image_loras = analysis_result.get("image_loras", [])
            if image_loras:
                # Add LoRAs to prompt using SD WebUI format: <lora:name:strength>
                lora_tags = []
                trigger_parts = []
                for lora in image_loras[:3]:  # Use top 3 image LoRAs
                    lora_id = lora.get("lora_id", "")
                    lora_name = lora.get("name", "")
                    lora_weight = lora.get("weight", 0.8)
                    lora_tags.append(f"<lora:{lora_id}:{lora_weight}>")
                    logger.info(f"Adding Image LoRA to T2I: {lora_name} (ID: {lora_id})")
                    # Collect trigger_words and trigger_prompt (controlled by stage1 switches)
                    inject_trigger_prompt_t2i = _get_config(req, "stage1_prompt_analysis", "inject_trigger_prompt", True)
                    inject_trigger_words_t2i = _get_config(req, "stage1_prompt_analysis", "inject_trigger_words", True)
                    import json as _json
                    if inject_trigger_words_t2i:
                        tw = lora.get("trigger_words", [])
                        if isinstance(tw, str):
                            try:
                                tw = _json.loads(tw)
                            except Exception:
                                tw = []
                        for word in (tw or []):
                            if word and word not in trigger_parts:
                                trigger_parts.append(word)
                    if inject_trigger_prompt_t2i:
                        tp = lora.get("trigger_prompt") or ""
                        if tp.strip() and tp.strip() not in trigger_parts:
                            trigger_parts.append(tp.strip())

                # Prepend trigger words/prompt, append LoRA tags
                if trigger_parts:
                    prompt = "\n\n".join(trigger_parts) + "\n\n" + prompt
                if lora_tags:
                    prompt = prompt + " " + " ".join(lora_tags)

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

        if req.aspect_ratio or True:  # Always calculate dimensions
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
        else:
            # 如果没有宽高比，使用配置的尺寸或默认值
            width = t2i_config.get("width", 832)
            height = t2i_config.get("height", 1216)

        steps = t2i_config.get("steps", 20)
        cfg_scale = t2i_config.get("cfg_scale", 7.0)
        sampler = t2i_config.get("sampler", "DPM++ 2M Karras")
        seed = t2i_config.get("seed", -1)

        logger.info(f"T2I generation at high resolution: {width}x{height}")

        # Call SD WebUI txt2img API
        payload = {
            "prompt": prompt,
            "negative_prompt": "low quality, blurry, distorted",
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


async def _generate_video(workflow_id: str, req, first_frame_url: Optional[str], analysis_result: Optional[dict], task_manager, is_continuation: bool = False, parent_workflow: dict = None) -> tuple[Optional[str], Optional[str]]:
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
            video_noise_aug = req.internal_config.get("stage4_video", {}).get("generation", {}).get("noise_aug_strength", 0.0)
        if video_noise_aug is None:
            video_noise_aug = 0.0

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

        # Continuation: override resolution with parent video's actual dimensions
        # to ensure the first frame is not cropped/distorted
        if is_continuation and parent_workflow:
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
                        logger.info(f"[{workflow_id}] Continuation: inherited resolution from parent '{parent_res}' -> {width}x{height}")
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
            image_mode = ImageMode.FIRST_FRAME

        # Build prompt
        prompt = req.user_prompt
        auto_prompt = _get_config(req, "stage1_prompt_analysis", "auto_prompt", True)
        if analysis_result and auto_prompt:
            optimized_i2v = analysis_result.get("optimized_i2v_prompt")
            if optimized_i2v:
                prompt = optimized_i2v

        # Build LORAs list
        loras = []
        auto_lora = _get_config(req, "stage1_prompt_analysis", "auto_lora", True)
        if analysis_result and auto_lora:
            video_loras = analysis_result.get("video_loras", [])

            # Determine if we're in I2V or T2V mode
            is_i2v_mode = image_mode in [ImageMode.FIRST_FRAME, ImageMode.FACE_REFERENCE, ImageMode.FULL_BODY_REFERENCE]

            # Filter LoRAs by mode
            # Pose-based LoRAs have no 'mode' field; noise_stage may be high/low/single.
            # For those, all returned video LoRAs are already appropriate (filtered at DB level).
            filtered_loras = []
            for lora in video_loras:
                lora_mode = lora.get("mode", "").upper()
                lora_noise = lora.get("noise_stage") or ""
                if is_i2v_mode:
                    # Accept I2V-tagged, any noise_stage (high/low/single), or no mode set
                    if lora_mode == "I2V" or lora_noise in ("high", "low", "single") or not lora_mode:
                        filtered_loras.append(lora)
                else:
                    # For T2V, prefer T2V LoRAs or no mode set
                    if lora_mode == "T2V" or not lora_mode:
                        filtered_loras.append(lora)

            # If still no filtered LoRAs, fall back to original list
            if not filtered_loras:
                filtered_loras = video_loras
                logger.warning(f"[{workflow_id}] No matching LoRAs found for mode {image_mode}, using all recommended")

            # Read trigger injection switches (from stage1 config)
            inject_trigger_prompt = _get_config(req, "stage1_prompt_analysis", "inject_trigger_prompt", True)
            inject_trigger_words = _get_config(req, "stage1_prompt_analysis", "inject_trigger_words", True)

            # Take top 3
            for lora in filtered_loras[:3]:
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
                logger.info(f"[{workflow_id}] Selected LoRA: {lora['name']} (mode={lora.get('mode')}, noise_stage={lora.get('noise_stage')})")

        # Default: Add instagirl_v2 LoRA when in T2V fallback mode (no first frame) and no LoRAs specified
        if not loras and not first_frame_url:
            loras.append(LoraInput(name="instagirl_v2", strength=0.8))
            logger.info(f"[{workflow_id}] T2V fallback with no LoRAs, adding default instagirl_v2 LoRA")

        # Download first frame for upload
        image_file = None
        if first_frame_url:
            # Handle both URL and local filename
            if first_frame_url.startswith('http://') or first_frame_url.startswith('https://'):
                # Remote URL — if it's a video, extract the first frame
                from api.services.video_frame_extractor import convert_video_url_to_frame
                converted_url = await convert_video_url_to_frame(first_frame_url)
                if converted_url != first_frame_url:
                    logger.info(f"[{workflow_id}] Converted video URL to frame: {first_frame_url} -> {converted_url}")
                    first_frame_url = converted_url
                # Download the (now guaranteed image) URL
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
                chain_req.mmaudio_steps = mmaudio_config.get("steps", 25)
                chain_req.mmaudio_cfg = mmaudio_config.get("cfg", 4.5)
                logger.info(f"[VIDEO_PARAMS] {workflow_id} - mmaudio enabled: steps={chain_req.mmaudio_steps}, cfg={chain_req.mmaudio_cfg}, prompt='{chain_req.mmaudio_prompt[:50]}'")

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

        import asyncio
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
