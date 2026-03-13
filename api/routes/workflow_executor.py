"""
Advanced workflow execution logic.

This module contains the async orchestration logic for the advanced workflow system.
"""
import logging
import base64
import json
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)


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
        if req.auto_analyze:
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
                await _update_stage(task_manager, workflow_id, "prompt_analysis", "completed", details="未启用自动分析")
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
                seedream_params = req.seedream_params or {}
                edit_mode = seedream_params.get("edit_mode", "face_wearings")
                enable_reactor = seedream_params.get("enable_reactor_first", True)
                details = f"编辑模式: {edit_mode}\n换脸: {'是' if enable_reactor else '否'}\n结果URL: {edited_frame_url}"
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
        video_params = req.video_params or {}
        details = f"Chain ID: {chain_id}\n"
        details += f"模型: {video_params.get('model', 'A14B')}\n"
        details += f"分辨率: {video_params.get('resolution', '720p_3:4')}\n"
        details += f"时长: {video_params.get('duration', '5s')}\n"
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

        # Get T2I parameters
        t2i_params = req.t2i_params or {}
        width = t2i_params.get("width", 832)
        height = t2i_params.get("height", 1216)
        steps = t2i_params.get("steps", 20)
        cfg_scale = t2i_params.get("cfg_scale", 7.0)
        sampler = t2i_params.get("sampler", "DPM++ 2M Karras")

        # Call SD WebUI txt2img API
        payload = {
            "prompt": prompt,
            "negative_prompt": "low quality, blurry, distorted",
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "sampler_name": sampler,
            "seed": -1
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

        # Get SeeDream parameters
        seedream_params = req.seedream_params or {}
        edit_mode = seedream_params.get("edit_mode", "face_wearings")
        enable_reactor = seedream_params.get("enable_reactor_first", True)

        # Call SeeDream edit endpoint
        edit_req = SeeDreamEditRequest(
            scene_image=first_frame_url,
            reference_face=req.reference_image,
            mode=edit_mode,
            enable_face_swap=enable_reactor,
            size="1024x1024"
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

        # Get video parameters
        video_params = req.video_params or {}
        model = ModelType(video_params.get("model", "A14B"))
        resolution = video_params.get("resolution", "720p_3:4")
        duration = video_params.get("duration", "5s")
        steps = video_params.get("steps", 20)
        cfg = video_params.get("cfg", 6.0)

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
        if analysis_result and req.auto_prompt:
            optimized_i2v = analysis_result.get("optimized_i2v_prompt")
            if optimized_i2v:
                prompt = optimized_i2v

        # Build LORAs list
        loras = []
        if analysis_result and req.auto_lora:
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
            loras=loras,
            image_mode=image_mode,
            auto_continue=False,
            segments=[ChainSegment(
                prompt=prompt,
                duration=duration_seconds,
                loras=[]
            )]
        )

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
