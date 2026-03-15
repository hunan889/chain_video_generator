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
        if key in ["model", "resolution", "duration", "steps", "cfg", "shift", "scheduler", "noise_aug_strength", "motion_amplitude"]:
            # These are under stage4_video.generation in internal_config
            pass  # Will be handled by internal_config check above
        elif req.video_params and key in req.video_params:
            result = req.video_params[key]
            source = "legacy"

    # Priority 3: Default value
    if result is None:
        result = default
        source = "default"

    logger.debug(f"[CONFIG] {stage}.{key} = {result} (from {source})")
    return result


def get_default_seedream_prompt(mode: str) -> str:
    """
    Get default SeeDream prompt based on edit mode.

    Args:
        mode: Edit mode (face_only, face_wearings, full_body)

    Returns:
        Default prompt string
    """
    prompts = {
        "face_only": "edit image 2, keep the position and pose of image 2, swap face to image 1, only change the face, keep everything else exactly the same including clothing, accessories, background",
        "face_wearings": "edit image 2, keep the position and pose of image 2, swap face to image 1, change face and accessories (jewelry, glasses, hair accessories) to match image 1, keep clothing and background the same",
        "full_body": "edit image 2, keep the position and pose of image 2, swap face to image 1, change face, accessories, and clothing to match image 1, keep background the same"
    }
    return prompts.get(mode, prompts["face_wearings"])


async def _apply_face_swap_to_frame(
    frame_url: str,
    reference_face: str,
    strength: float = 1.0,
    task_manager = None
) -> Optional[str]:
    """
    Apply face swap to a single frame using Reactor.

    Args:
        frame_url: URL of the frame image
        reference_face: Reference face image (base64 or URL)
        strength: Face swap strength (0.0-1.0)
        task_manager: TaskManager instance

    Returns:
        URL of the face-swapped image, or None if failed
    """
    try:
        import requests as http_requests
        from api.config import FORGE_URL
        from api.services import storage
        import uuid

        # Download frame image
        async with aiohttp.ClientSession() as session:
            async with session.get(frame_url) as resp:
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
                async with session.get(reference_face) as resp:
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
            "restorer_visibility": 1,
            "codeformer_weight": 0.7,
            "restore_first": 1,
            "upscaler": "None",
            "scale": 1,
            "upscale_visibility": 1,
            "device": "CUDA",
            "mask_face": 1,
            "det_thresh": 0.5,
            "det_maxnum": 0,
        }

        logger.info(f"Applying face swap to frame with strength={strength}")
        reactor_resp = http_requests.post(
            f"{FORGE_URL}/reactor/image", json=reactor_payload, timeout=120
        )

        if reactor_resp.status_code == 200:
            swapped_b64 = reactor_resp.json()["image"]
            swapped_data = base64.b64decode(swapped_b64)

            # Save result
            filename = f"face_swap_{uuid.uuid4().hex[:8]}.png"
            local_path, url = await storage.save_upload(swapped_data, filename)

            logger.info(f"Face swap completed: {url}")
            return url
        else:
            logger.error(f"Reactor failed: {reactor_resp.status_code}")
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
        # Validate configuration based on mode
        if req.mode in ["face_reference", "full_body_reference"]:
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

        if auto_analyze:
            analysis_result = await _analyze_prompt(req, task_manager)
            if analysis_result:
                await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
                    "analysis_result": json.dumps(analysis_result)
                })

                # 保存结构化详细信息
                details_dict = {
                    "video_loras": analysis_result.get('video_loras', []),
                    "image_loras": analysis_result.get('image_loras', []),
                    "original_prompt": req.user_prompt,
                    "optimized_prompt": analysis_result.get('optimized_i2v_prompt') or analysis_result.get('optimized_t2i_prompt')
                }

                await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details_dict=details_dict)
            else:
                await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details_dict={"error": "分析失败"})
        else:
            await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details_dict={"skipped": True, "reason": "未启用"})

        await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed")

        # Stage 2: First Frame Acquisition
        await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "running")
        await task_manager.redis.hset(f"workflow:{workflow_id}", "current_stage", "first_frame_acquisition")

        first_frame_url = await _acquire_first_frame(workflow_id, req, analysis_result, task_manager)
        if not first_frame_url:
            raise Exception("Failed to acquire first frame")

        await task_manager.redis.hset(f"workflow:{workflow_id}", "first_frame_url", first_frame_url)

        # 保存结构化详细信息
        if req.mode == "first_frame":
            source_text = "upload"
        else:
            first_frame_source_for_display = req.first_frame_source or _get_config(req, "stage2_first_frame", "first_frame_source", "select_existing")
            source_text = first_frame_source_for_display

        details_dict = {
            "source": source_text,
            "url": first_frame_url,
            "face_swapped": False
        }

        # Stage 2.1: 首帧换脸（可选）
        face_swap_config = _get_config(req, "stage2_first_frame", "face_swap", {})
        if face_swap_config.get("enabled") and req.reference_image:
            logger.info("Applying face swap to first frame")
            swapped_url = await _apply_face_swap_to_frame(
                first_frame_url,
                req.reference_image,
                strength=face_swap_config.get("strength", 1.0),
                task_manager=task_manager
            )
            if swapped_url:
                first_frame_url = swapped_url
                await task_manager.redis.hset(f"workflow:{workflow_id}", "first_frame_url", first_frame_url)
                details_dict["face_swapped"] = True
                details_dict["face_swap_strength"] = face_swap_config.get("strength", 1.0)
                details_dict["url"] = first_frame_url
        elif face_swap_config.get("enabled") and not req.reference_image:
            # Face swap enabled but no reference image provided
            details_dict["face_swap_skipped"] = True
            details_dict["face_swap_skip_reason"] = "未提供参考图片 (reference_image)"
            logger.warning(f"[{workflow_id}] Face swap enabled but no reference_image provided")

        await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "completed", details_dict=details_dict)

        # Stage 3: SeeDream Editing
        await _update_stage(task_manager, workflow_id, "seedream_edit", "running")
        await task_manager.redis.hset(f"workflow:{workflow_id}", "current_stage", "seedream_edit")

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
            # 获取首帧图片的实际尺寸
            try:
                from PIL import Image
                import io

                # 下载首帧图片获取尺寸
                if first_frame_url.startswith('http'):
                    async with aiohttp.ClientSession() as session:
                        async with session.get(first_frame_url) as resp:
                            if resp.status == 200:
                                image_data = await resp.read()
                                img = Image.open(io.BytesIO(image_data))
                                detected_size = f"{img.width}x{img.height}"
                            else:
                                detected_size = "832x1216"  # fallback
                else:
                    # 本地文件路径
                    local_path = UPLOADS_DIR / first_frame_url.split('/')[-1]
                    if local_path.exists():
                        img = Image.open(local_path)
                        detected_size = f"{img.width}x{img.height}"
                    else:
                        detected_size = "832x1216"  # fallback
            except Exception as e:
                logger.warning(f"Failed to detect image size: {e}, using default")
                detected_size = "832x1216"

            # 保存 SeeDream 参数（在开始时就显示）
            edit_mode = _get_config(req, "stage3_seedream", "mode", "face_wearings")
            enable_reactor = _get_config(req, "stage3_seedream", "enable_reactor", True)
            custom_prompt = _get_config(req, "stage3_seedream", "prompt", None)
            strength = _get_config(req, "stage3_seedream", "strength", 0.8)
            seed = _get_config(req, "stage3_seedream", "seed", None)
            # 优先使用配置的尺寸，如果没有配置则使用检测到的尺寸
            size = _get_config(req, "stage3_seedream", "size", detected_size)

            # 在 running 状态时就保存参数
            running_details = {
                "mode": edit_mode,
                "enable_reactor": enable_reactor,
                "prompt": custom_prompt or get_default_seedream_prompt(edit_mode),
                "strength": strength,
                "seed": seed,
                "size": size,
                "reference_image": req.reference_image,
                "first_frame_url": first_frame_url
            }
            await _update_stage(task_manager, workflow_id, "seedream_edit", "running", details_dict=running_details)

            edit_result = await _edit_first_frame(workflow_id, req, first_frame_url, size, task_manager)
            if edit_result:
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
            else:
                running_details["error"] = "编辑失败，使用原图"
                running_details["api_status"] = "failed"
                running_details["fallback_used"] = True
                running_details["fallback_reason"] = "SeeDream 调用异常"
                edited_frame_url = first_frame_url
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

        # 在 running 状态时就保存所有参数
        running_details = {
            "model": video_model,
            "resolution": video_resolution,
            "duration": video_duration,
            "face_swap_enabled": face_swap_enabled,
            "upscale_enabled": upscale_enabled,
            "interpolation_enabled": interp_enabled,
            "first_frame_url": edited_frame_url,
            "prompt": req.user_prompt
        }
        await _update_stage(task_manager, workflow_id, "video_generation", "running", details_dict=running_details)

        chain_id, final_video_url, loras_info = await _generate_video(workflow_id, req, edited_frame_url, analysis_result, task_manager)

        if chain_id:
            await task_manager.redis.hset(f"workflow:{workflow_id}", "chain_id", chain_id)
        if final_video_url:
            await task_manager.redis.hset(f"workflow:{workflow_id}", "final_video_url", final_video_url)

        # 完成时添加结果和 LoRA 信息
        running_details["chain_id"] = chain_id
        running_details["video_url"] = final_video_url
        if loras_info:
            running_details["loras"] = loras_info

        await _update_stage(task_manager, workflow_id, "video_generation", "completed", details_dict=running_details)

        # Mark workflow as completed
        await task_manager.redis.hset(f"workflow:{workflow_id}", "status", "completed")
        logger.info(f"Workflow {workflow_id} completed successfully")

    except Exception as e:
        logger.error(f"Workflow {workflow_id} failed: {e}", exc_info=True)
        await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
            "status": "failed",
            "error": str(e)
        })


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
    """Call /workflow/analyze endpoint internally"""
    try:
        from api.routes.workflow import analyze_workflow, WorkflowAnalyzeRequest

        analyze_req = WorkflowAnalyzeRequest(
            prompt=req.user_prompt,
            mode=req.mode,
            top_k_image_loras=5,
            top_k_video_loras=5
        )

        result = await analyze_workflow(analyze_req, _=None)
        return result.model_dump()

    except Exception as e:
        logger.error(f"Prompt analysis failed: {e}", exc_info=True)
        return None


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
        # In first_frame mode, uploaded image is always used as first frame
        # In face_reference/full_body_reference modes, use first_frame_source from config or request
        if req.mode != "first_frame":
            first_frame_source = req.first_frame_source or _get_config(req, "stage2_first_frame", "first_frame_source", "select_existing")
            logger.info(f"[{workflow_id}] Mode is {req.mode}, first_frame_source: {first_frame_source}")

        if req.mode == "first_frame":
            # Use uploaded first frame (only in first_frame mode)
            if not req.uploaded_first_frame:
                raise Exception("uploaded_first_frame is required when mode=first_frame")

            # Handle different input formats
            if req.uploaded_first_frame.startswith('data:image'):
                # Base64 data URL
                image_b64 = req.uploaded_first_frame.split(',')[1]
                image_data = base64.b64decode(image_b64)
                filename = f"first_frame_{workflow_id}.png"
                local_path, url = await storage.save_upload(image_data, filename)
                return url
            elif req.uploaded_first_frame.startswith('http://') or req.uploaded_first_frame.startswith('https://'):
                # Remote URL - download it
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
            # Generate first frame using T2I (SD WebUI)
            return await _generate_t2i_image(req, analysis_result, task_manager)

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
                raise Exception("No recommended images found for select_existing mode")

            # Select the first (highest similarity) result
            selected_image = recommended_images[0]
            selected_url = selected_image.get("url")
            logger.info(f"[{workflow_id}] Auto-selected resource: {selected_url} (similarity: {selected_image.get('similarity', 0.0):.3f})")

            # Calculate target dimensions from resolution and aspect_ratio
            resolution_map = {
                '480p': {'width': 854, 'height': 480},
                '720p': {'width': 1280, 'height': 720},
                '1080p': {'width': 1920, 'height': 1080}
            }
            base_res = resolution_map.get(req.resolution, resolution_map['720p'])

            # Parse aspect ratio
            ar_parts = req.aspect_ratio.split(':')
            ar_width = int(ar_parts[0])
            ar_height = int(ar_parts[1])

            # Calculate actual dimensions based on aspect ratio
            if ar_width / ar_height > base_res['width'] / base_res['height']:
                width = base_res['width']
                height = round(base_res['width'] * ar_height / ar_width)
            else:
                height = base_res['height']
                width = round(base_res['height'] * ar_width / ar_height)

            # Ensure dimensions are multiples of 8
            width = round(width / 8) * 8
            height = round(height / 8) * 8

            # Convert video to first frame if needed
            selected_url = await convert_video_url_to_frame(selected_url, width, height)

            return selected_url

        else:
            raise Exception(f"Unknown first_frame_source: {first_frame_source}")

    except Exception as e:
        logger.error(f"First frame acquisition failed: {e}", exc_info=True)
        return None


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
                for lora in image_loras[:3]:  # Use top 3 image LoRAs
                    lora_id = lora.get("lora_id", "")
                    lora_name = lora.get("name", "")
                    # Use strength 0.8 as default
                    lora_tags.append(f"<lora:{lora_id}:0.8>")
                    logger.info(f"Adding Image LoRA to T2I: {lora_name} (ID: {lora_id})")

                # Append LoRA tags to prompt
                if lora_tags:
                    prompt = prompt + " " + " ".join(lora_tags)

        # Get T2I parameters from internal_config or legacy params
        t2i_config = _get_config(req, "stage2_first_frame", "t2i", {})
        if not t2i_config and req.t2i_params:
            t2i_config = req.t2i_params

        width = t2i_config.get("width", 832)
        height = t2i_config.get("height", 1216)
        steps = t2i_config.get("steps", 20)
        cfg_scale = t2i_config.get("cfg_scale", 7.0)
        sampler = t2i_config.get("sampler", "DPM++ 2M Karras")
        seed = t2i_config.get("seed", -1)

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

        # Get SeeDream parameters from internal_config or legacy params
        edit_mode = _get_config(req, "stage3_seedream", "mode", "face_wearings")
        enable_reactor = _get_config(req, "stage3_seedream", "enable_reactor", True)
        custom_prompt = _get_config(req, "stage3_seedream", "prompt", None)
        strength = _get_config(req, "stage3_seedream", "strength", 0.8)
        seed = _get_config(req, "stage3_seedream", "seed", None)
        # Use the passed size parameter (already detected from first frame)

        # Use custom prompt or default prompt
        if custom_prompt:
            prompt = custom_prompt
        else:
            prompt = get_default_seedream_prompt(edit_mode)

        # Call SeeDream edit endpoint
        edit_req = SeeDreamEditRequest(
            scene_image=first_frame_url,
            reference_face=req.reference_image,
            mode=edit_mode,
            enable_face_swap=enable_reactor,
            prompt=prompt,
            size=size,
            seed=seed
        )

        result = await seedream_edit(edit_req, _=None)
        return result  # Return full result object with debug info

    except Exception as e:
        logger.error(f"SeeDream editing failed: {e}", exc_info=True)
        # Return None to indicate failure
        return None


async def _generate_video(workflow_id: str, req, first_frame_url: str, analysis_result: Optional[dict], task_manager) -> tuple[Optional[str], Optional[str]]:
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
            video_resolution = req.internal_config.get("stage4_video", ).get("generation", {}).get("resolution", "480p_3:4")
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
            clip_preset = req.internal_config.get("stage4_video", ).get("generation", {}).get("clip_preset", "nsfw")
        if clip_preset is None:
            clip_preset = "nsfw"

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

        # Parse resolution (must be multiples of 16 for VAE)
        if resolution == "720p_3:4":
            width, height = 608, 832
        elif resolution == "720p_16:9":
            width, height = 1280, 720
        elif resolution == "1080p_16:9":
            width, height = 1920, 1080
        elif resolution == "480p_3:4" or resolution == "480p_3_4":
            width, height = 352, 480  # 352 is closest 16-multiple to 360 (3:4 ratio ≈ 0.73)
        elif resolution == "480p_16:9" or resolution == "480p_16_9":
            width, height = 832, 480
        else:
            # Default fallback
            width, height = 832, 480
            logger.warning(f"[{workflow_id}] Unknown resolution '{resolution}', using default 832x480")

        # Parse duration
        duration_seconds = float(duration.rstrip('s'))

        # Determine image mode based on workflow mode (MUST be before LoRA filtering)
        if req.mode == "face_reference":
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
            filtered_loras = []
            for lora in video_loras:
                lora_mode = lora.get("mode", "").upper()
                if is_i2v_mode:
                    # For I2V, prefer I2V LoRAs or single-stage LoRAs
                    if lora_mode == "I2V" or lora.get("noise_stage") == "single":
                        filtered_loras.append(lora)
                else:
                    # For T2V, prefer T2V LoRAs
                    if lora_mode == "T2V":
                        filtered_loras.append(lora)

            # If no filtered LoRAs, fall back to original list
            if not filtered_loras:
                filtered_loras = video_loras
                logger.warning(f"[{workflow_id}] No matching LoRAs found for mode {image_mode}, using all recommended")

            # Take top 3
            for lora in filtered_loras[:3]:
                loras.append(LoraInput(
                    name=str(lora["lora_id"]),
                    strength=0.8
                ))
                logger.info(f"[{workflow_id}] Selected LoRA: {lora['name']} (mode={lora.get('mode')}, noise_stage={lora.get('noise_stage')})")

        # Download first frame for upload
        # Handle both URL and local filename
        if first_frame_url.startswith('http://') or first_frame_url.startswith('https://'):
            # Remote URL - download it
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
        elif first_frame_url.startswith('/uploads/'):
            # Uploads path - extract filename and read from UPLOADS_DIR
            filename = first_frame_url.split('/')[-1]
            local_path = UPLOADS_DIR / filename
            if not local_path.exists():
                raise Exception(f"First frame file not found in uploads: {first_frame_url}")
            image_data = local_path.read_bytes()
            logger.info(f"[{workflow_id}] Read first frame from uploads: {local_path}")
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
                chain_req.upscale_model = upscale_config.get("model", "RealESRGAN_x4plus")
                chain_req.upscale_resize = upscale_config.get("resize", 2.0)
                logger.info(f"[VIDEO_PARAMS] {workflow_id} - upscale enabled: model={chain_req.upscale_model}, resize={chain_req.upscale_resize}")

            # Interpolation
            interp_config = postprocess_config.get("interpolation", {})
            if interp_config.get("enabled"):
                chain_req.enable_interpolation = True
                chain_req.interpolation_multiplier = interp_config.get("multiplier", 2)
                chain_req.interpolation_profile = interp_config.get("profile", "auto")
                logger.info(f"[VIDEO_PARAMS] {workflow_id} - interpolation enabled: multiplier={chain_req.interpolation_multiplier}, profile={chain_req.interpolation_profile}")

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
        final_video_url = None

        import asyncio
        for _ in range(60):  # Poll for up to 5 minutes
            await asyncio.sleep(5)

            chain_data = await task_manager.redis.hgetall(f"chain:{chain_id}")
            status = chain_data.get("status")

            if status == "completed":
                final_video_url = chain_data.get("final_video_url")
                logger.info(f"[{workflow_id}] Chain completed, video URL: {final_video_url}")
                break
            elif status == "failed":
                error = chain_data.get("error", "Unknown error")
                logger.error(f"[{workflow_id}] Chain failed: {error}")
                raise Exception(f"Chain generation failed: {error}")

        if not final_video_url:
            logger.warning(f"[{workflow_id}] Chain polling timeout, status: {status}")

        # Return chain_id, final_video_url, and loras list
        loras_info = [{"name": l.name, "strength": l.strength} for l in loras]
        return chain_id, final_video_url, loras_info

    except Exception as e:
        logger.error(f"[{workflow_id}] Video generation failed: {e}", exc_info=True)
        return None, None, []
