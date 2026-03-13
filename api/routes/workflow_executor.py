"""
Advanced workflow execution logic.

This module contains the async orchestration logic for the advanced workflow system.
"""
import logging
import base64
import json
from typing import Optional, Any
import aiohttp

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
    # Priority 1: internal_config
    if req.internal_config and stage in req.internal_config:
        stage_config = req.internal_config[stage]
        if key in stage_config:
            return stage_config[key]

    # Priority 2: Legacy parameters
    if stage == "stage1_prompt_analysis":
        if key == "auto_analyze":
            return req.auto_analyze
        elif key == "auto_lora":
            return req.auto_lora
        elif key == "auto_prompt":
            return req.auto_prompt

    elif stage == "stage2_first_frame":
        if key == "first_frame_source":
            return req.first_frame_source.value
        elif key == "t2i" and req.t2i_params:
            return req.t2i_params

    elif stage == "stage3_seedream":
        if req.seedream_params:
            if key == "mode":
                return req.seedream_params.get("edit_mode", default)
            elif key == "enable_reactor":
                return req.seedream_params.get("enable_reactor_first", default)
            elif key in req.seedream_params:
                return req.seedream_params[key]

    elif stage == "stage4_video":
        if req.video_params and key in req.video_params:
            return req.video_params[key]

    # Priority 3: Default value
    return default


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
        elif reference_face.startswith('http'):
            async with aiohttp.ClientSession() as session:
                async with session.get(reference_face) as resp:
                    if resp.status != 200:
                        logger.error(f"Failed to download reference face: {resp.status}")
                        return None
                    face_data = await resp.read()
            face_b64 = base64.b64encode(face_data).decode()
        else:
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

                # 保存详细信息
                details = f"推荐Video LORAs: {len(analysis_result.get('video_loras', []))}个\n"
                details += f"推荐Image LORAs: {len(analysis_result.get('image_loras', []))}个\n"
                if analysis_result.get('optimized_t2i_prompt'):
                    details += f"T2I优化Prompt: {analysis_result['optimized_t2i_prompt'][:100]}...\n"
                if analysis_result.get('optimized_i2v_prompt'):
                    details += f"I2V优化Prompt: {analysis_result['optimized_i2v_prompt'][:100]}..."

                await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details=details)
            else:
                await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details="分析失败")
        else:
            await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details="跳过（未启用）")

        await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed")

        # Stage 2: First Frame Acquisition
        await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "running")
        await task_manager.redis.hset(f"workflow:{workflow_id}", "current_stage", "first_frame_acquisition")

        first_frame_url = await _acquire_first_frame(workflow_id, req, analysis_result, task_manager)
        if not first_frame_url:
            raise Exception("Failed to acquire first frame")

        await task_manager.redis.hset(f"workflow:{workflow_id}", "first_frame_url", first_frame_url)

        # 保存详细信息
        source_text = {
            "use_uploaded": "使用上传图片",
            "generate": "T2I生成",
            "select_existing": "选择已有图片"
        }.get(req.first_frame_source.value, req.first_frame_source.value)
        details = f"来源: {source_text}\nURL: {first_frame_url}"

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
                details += f"\n首帧换脸: 已应用 (强度: {face_swap_config.get('strength', 1.0)})"
            else:
                details += "\n首帧换脸: 失败，使用原图"

        await _update_stage(task_manager, workflow_id, "first_frame_acquisition", "completed", details=details)

        # Stage 3: SeeDream Editing
        await _update_stage(task_manager, workflow_id, "seedream_edit", "running")
        await task_manager.redis.hset(f"workflow:{workflow_id}", "current_stage", "seedream_edit")

        edited_frame_url = first_frame_url
        if req.mode in ["face_reference", "full_body_reference"] and req.reference_image:
            edited_frame_url = await _edit_first_frame(workflow_id, req, first_frame_url, task_manager)
            if edited_frame_url:
                await task_manager.redis.hset(f"workflow:{workflow_id}", "edited_frame_url", edited_frame_url)

                # 保存详细信息
                edit_mode = _get_config(req, "stage3_seedream", "mode", "face_wearings")
                enable_reactor = _get_config(req, "stage3_seedream", "enable_reactor", True)
                custom_prompt = _get_config(req, "stage3_seedream", "prompt", None)

                details = f"编辑模式: {edit_mode}\n换脸: {'是' if enable_reactor else '否'}\n"
                if custom_prompt:
                    details += f"自定义Prompt: {custom_prompt[:80]}...\n"
                else:
                    details += f"默认Prompt: {get_default_seedream_prompt(edit_mode)[:80]}...\n"
                details += f"结果URL: {edited_frame_url}"

                await _update_stage(task_manager, workflow_id, "seedream_edit", "completed", details=details)
            else:
                await _update_stage(task_manager, workflow_id, "seedream_edit", "completed", details="编辑失败，使用原图")
        else:
            await _update_stage(task_manager, workflow_id, "seedream_edit", "completed", details="跳过（首帧模式）")

        await _update_stage(task_manager, workflow_id, "seedream_edit", "completed")

        # Stage 4: Video Generation
        await _update_stage(task_manager, workflow_id, "video_generation", "running")
        await task_manager.redis.hset(f"workflow:{workflow_id}", "current_stage", "video_generation")

        chain_id, final_video_url = await _generate_video(workflow_id, req, edited_frame_url, analysis_result, task_manager)

        if chain_id:
            await task_manager.redis.hset(f"workflow:{workflow_id}", "chain_id", chain_id)
        if final_video_url:
            await task_manager.redis.hset(f"workflow:{workflow_id}", "final_video_url", final_video_url)

        # 保存详细信息
        video_model = _get_config(req, "stage4_video", "model", "A14B")
        video_resolution = _get_config(req, "stage4_video", "resolution", "720p_3:4")
        video_duration = _get_config(req, "stage4_video", "duration", "5s")

        # 检查视频换脸配置
        video_face_swap_config = _get_config(req, "stage4_video", "face_swap", {})
        face_swap_enabled = video_face_swap_config.get("enabled", False) if isinstance(video_face_swap_config, dict) else False

        # 检查后处理配置
        postprocess_config = _get_config(req, "stage4_video", "postprocess", {})
        upscale_enabled = postprocess_config.get("upscale", {}).get("enabled", False) if isinstance(postprocess_config, dict) else False
        interp_enabled = postprocess_config.get("interpolation", {}).get("enabled", False) if isinstance(postprocess_config, dict) else False

        details = f"Chain ID: {chain_id}\n"
        details += f"模型: {video_model}\n"
        details += f"分辨率: {video_resolution}\n"
        details += f"时长: {video_duration}\n"
        if face_swap_enabled:
            details += f"视频换脸: 已启用\n"
        if upscale_enabled:
            details += f"超分: 已启用\n"
        if interp_enabled:
            details += f"插帧: 已启用\n"
        details += f"视频URL: {final_video_url}"
        await _update_stage(task_manager, workflow_id, "video_generation", "completed", details=details)

        # Mark workflow as completed
        await task_manager.redis.hset(f"workflow:{workflow_id}", "status", "completed")
        logger.info(f"Workflow {workflow_id} completed successfully")

    except Exception as e:
        logger.error(f"Workflow {workflow_id} failed: {e}", exc_info=True)
        await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
            "status": "failed",
            "error": str(e)
        })


async def _update_stage(task_manager, workflow_id: str, stage_name: str, status: str, error: str = None, details: str = None):
    """Update stage status in Redis"""
    mapping = {f"stage_{stage_name}": status}
    if error:
        mapping[f"stage_{stage_name}_error"] = error
    if details:
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
        from api.config import UPLOADS_DIR
        from api.services import storage
        import uuid

        if req.first_frame_source == "use_uploaded":
            # Use uploaded first frame
            if not req.uploaded_first_frame:
                raise Exception("uploaded_first_frame is required when first_frame_source=use_uploaded")

            # Decode and save uploaded image
            if req.uploaded_first_frame.startswith('data:image'):
                image_b64 = req.uploaded_first_frame.split(',')[1]
                image_data = base64.b64decode(image_b64)
            elif req.uploaded_first_frame.startswith('http'):
                # Download from URL
                async with aiohttp.ClientSession() as session:
                    async with session.get(req.uploaded_first_frame) as resp:
                        if resp.status != 200:
                            raise Exception(f"Failed to download uploaded frame: {resp.status}")
                        image_data = await resp.read()
            else:
                image_data = base64.b64decode(req.uploaded_first_frame)

            filename = f"first_frame_{workflow_id}.png"
            local_path, url = await storage.save_upload(image_data, filename)
            return url

        elif req.first_frame_source == "generate":
            # Generate first frame using T2I (SD WebUI)
            return await _generate_t2i_image(req, analysis_result, task_manager)

        elif req.first_frame_source == "select_existing":
            # Use selected existing image
            if not req.selected_image_url:
                raise Exception("selected_image_url is required when first_frame_source=select_existing")
            return req.selected_image_url

        else:
            raise Exception(f"Unknown first_frame_source: {req.first_frame_source}")

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

        logger.info(f"Generating T2I image: {prompt[:100]}...")

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


async def _edit_first_frame(workflow_id: str, req, first_frame_url: str, task_manager) -> Optional[str]:
    """
    Edit first frame using SeeDream.

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
            size="1024x1024",
            seed=seed
        )

        result = await seedream_edit(edit_req, _=None)
        return result.url

    except Exception as e:
        logger.error(f"SeeDream editing failed: {e}", exc_info=True)
        # Return original frame as fallback
        return first_frame_url


async def _generate_video(workflow_id: str, req, first_frame_url: str, analysis_result: Optional[dict], task_manager) -> tuple[Optional[str], Optional[str]]:
    """
    Generate video using Chain workflow.

    Returns: (chain_id, final_video_url)
    """
    try:
        from api.routes.extend import generate_chain
        from api.models.schemas import AutoChainRequest, ChainSegment, LoraInput
        from api.models.enums import ModelType, ImageMode
        import aiohttp
        from io import BytesIO
        from fastapi import UploadFile

        # Get video parameters from internal_config or legacy params
        video_model = _get_config(req, "stage4_video", "model", "A14B")
        video_resolution = _get_config(req, "stage4_video", "resolution", "720p_3:4")
        video_duration = _get_config(req, "stage4_video", "duration", "5s")
        video_steps = _get_config(req, "stage4_video", "steps", 20)
        video_cfg = _get_config(req, "stage4_video", "cfg", 6.0)
        video_shift = _get_config(req, "stage4_video", "shift", 5.0)
        video_scheduler = _get_config(req, "stage4_video", "scheduler", "unipc")
        video_noise_aug = _get_config(req, "stage4_video", "noise_aug_strength", 0.0)
        video_motion_amp = _get_config(req, "stage4_video", "motion_amplitude", 0.0)

        model = ModelType(video_model)
        resolution = video_resolution
        duration = video_duration
        steps = video_steps
        cfg = video_cfg

        # Parse resolution
        if resolution == "720p_3:4":
            width, height = 608, 832
        elif resolution == "720p_16:9":
            width, height = 1280, 720
        elif resolution == "1080p_16:9":
            width, height = 1920, 1080
        else:
            width, height = 832, 480

        # Parse duration
        duration_seconds = float(duration.rstrip('s'))

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
            for lora in video_loras[:3]:  # Top 3
                loras.append(LoraInput(
                    name=lora["lora_id"],
                    strength=0.8
                ))

        # Determine image mode based on workflow mode
        if req.mode == "face_reference":
            image_mode = ImageMode.FACE_REFERENCE
        elif req.mode == "full_body_reference":
            image_mode = ImageMode.FULL_BODY_REFERENCE
        else:
            image_mode = ImageMode.FIRST_FRAME

        # Download first frame for upload
        async with aiohttp.ClientSession() as session:
            async with session.get(first_frame_url) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to download first frame: {resp.status}")
                image_data = await resp.read()

        # Create UploadFile object
        image_file = UploadFile(
            filename="first_frame.png",
            file=BytesIO(image_data)
        )

        # Build chain request
        chain_req = AutoChainRequest(
            prompt=prompt,
            model=model,
            width=width,
            height=height,
            duration=duration_seconds,
            steps=steps,
            cfg=cfg,
            shift=video_shift,
            scheduler=video_scheduler,
            loras=loras,
            image_mode=image_mode,
            auto_continue=False,
            noise_aug_strength=video_noise_aug,
            motion_amplitude=video_motion_amp,
            segments=[ChainSegment(
                prompt=prompt,
                duration=duration_seconds,
                loras=[]
            )]
        )

        # Apply video face swap configuration
        video_face_swap_config = _get_config(req, "stage4_video", "face_swap", {})
        if isinstance(video_face_swap_config, dict) and video_face_swap_config.get("enabled"):
            # Set face swap mode
            face_swap_mode = video_face_swap_config.get("mode", "face_reference")
            if face_swap_mode == "face_reference":
                chain_req.image_mode = ImageMode.FACE_REFERENCE
            elif face_swap_mode == "full_body_reference":
                chain_req.image_mode = ImageMode.FULL_BODY_REFERENCE

            # Set face swap strength
            chain_req.face_swap_strength = video_face_swap_config.get("strength", 1.0)

        # Apply postprocess configuration
        postprocess_config = _get_config(req, "stage4_video", "postprocess", {})
        if isinstance(postprocess_config, dict):
            # Upscale
            upscale_config = postprocess_config.get("upscale", {})
            if upscale_config.get("enabled"):
                chain_req.enable_upscale = True
                chain_req.upscale_model = upscale_config.get("model", "RealESRGAN_x4plus")
                chain_req.upscale_resize = upscale_config.get("resize", 2.0)

            # Interpolation
            interp_config = postprocess_config.get("interpolation", {})
            if interp_config.get("enabled"):
                chain_req.enable_interpolation = True
                chain_req.interpolation_multiplier = interp_config.get("multiplier", 2)
                chain_req.interpolation_profile = interp_config.get("profile", "auto")

        # Call chain generation endpoint
        params_json = chain_req.model_dump_json()
        result = await generate_chain(
            image=image_file,
            face_image=None,
            initial_reference_image=None,
            params=params_json,
            _=None
        )

        # Poll for completion
        chain_id = result.chain_id
        final_video_url = None

        import asyncio
        for _ in range(60):  # Poll for up to 5 minutes
            await asyncio.sleep(5)

            chain_data = await task_manager.redis.hgetall(f"chain:{chain_id}")
            status = chain_data.get("status")

            if status == "completed":
                final_video_url = chain_data.get("final_video_url")
                break
            elif status == "failed":
                error = chain_data.get("error", "Unknown error")
                raise Exception(f"Chain generation failed: {error}")

        return chain_id, final_video_url

    except Exception as e:
        logger.error(f"Video generation failed: {e}", exc_info=True)
        return None, None
