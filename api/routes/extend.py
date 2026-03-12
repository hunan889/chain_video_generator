import logging
import math
import json
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Body
from api.models.schemas import (
    ExtendRequest, AutoChainRequest, ChainSegment, ChainResponse,
    GenerateResponse, LoraInput,
)
from api.models.enums import GenerateMode, ModelType, TaskStatus
from api.middleware.auth import verify_api_key
from api.services.workflow_builder import build_workflow, _inject_trigger_words
from api.services.prompt_splitter import split_prompt_by_segments
from api.services import storage

logger = logging.getLogger(__name__)
router = APIRouter()


def _duration_to_frames(duration: float, fps: int) -> int:
    """Convert duration to frame count aligned to 4n+1."""
    f = max(round(duration * fps), 1)
    f = round((f - 1) / 4) * 4 + 1
    return max(f, 5)


@router.post("/generate/extend", response_model=GenerateResponse)
async def extend_video(req: ExtendRequest, _=Depends(verify_api_key)):
    """Extend a completed video by extracting its last frame as I2V input."""
    from api.main import task_manager

    # Get parent task
    parent = await task_manager.get_task(req.parent_task_id)
    if not parent:
        raise HTTPException(404, "Parent task not found")
    if parent["status"] != TaskStatus.COMPLETED.value:
        raise HTTPException(400, "Parent task is not completed")
    if not parent.get("video_url"):
        raise HTTPException(400, "Parent task has no video output")

    parent_params = parent.get("params") or {}

    # Inherit parameters from parent if not specified
    model = ModelType(parent_params.get("model", "a14b"))
    model_preset = parent_params.get("model_preset", "")
    t5_preset = parent_params.get("t5_preset", "")
    width = parent_params.get("width", 832)
    height = parent_params.get("height", 480)
    fps = parent_params.get("fps", 24)
    num_frames = req.num_frames or parent_params.get("num_frames", 81)
    steps = req.steps if req.steps is not None else parent_params.get("steps", 20)
    cfg = req.cfg if req.cfg is not None else parent_params.get("cfg", 6.0)
    shift = req.shift if req.shift is not None else parent_params.get("shift", 5.0)
    seed = req.seed
    scheduler = req.scheduler or parent_params.get("scheduler", "unipc")
    loras_raw = req.loras if req.loras is not None else [
        LoraInput(**l) for l in parent_params.get("loras", [])
    ]

    # Extract last frame from parent video
    from api.services.ffmpeg_utils import extract_last_frame
    video_path = await storage.get_video_path_from_url(parent["video_url"])
    if not video_path:
        raise HTTPException(400, "Parent video file not found on disk")

    frame_path = await extract_last_frame(video_path)
    frame_data = frame_path.read_bytes()

    # Upload frame to ComfyUI
    client = task_manager.clients.get(model.value)
    if not client or not await client.is_alive():
        raise HTTPException(503, f"ComfyUI {model.value} instance is not available")

    upload_result = await client.upload_image(frame_data, frame_path.name)
    comfy_filename = upload_result.get("name", frame_path.name)

    # Auto prompt optimization
    prompt = req.prompt
    params_extra = {}
    if req.auto_prompt:
        from api.routes.generate import _optimize_prompt
        original_prompt = prompt
        duration = int(num_frames / fps) if num_frames else 3
        optimized = await _optimize_prompt(prompt, loras_raw, "i2v", duration)
        if optimized:
            prompt = optimized
        params_extra["ai_prompt"] = prompt
        params_extra["original_prompt"] = original_prompt

    workflow = build_workflow(
        mode=GenerateMode.I2V, model=model,
        prompt=prompt, negative_prompt=req.negative_prompt,
        width=width, height=height, num_frames=num_frames, fps=fps,
        steps=steps, cfg=cfg, shift=shift, seed=seed,
        loras=loras_raw, scheduler=scheduler,
        model_preset=model_preset, image_filename=comfy_filename,
        noise_aug_strength=req.noise_aug_strength,
        motion_amplitude=parent_params.get("motion_amplitude", 0.0),
        color_match=parent_params.get("color_match", True),
        color_match_method=parent_params.get("color_match_method", "mkl"),
        resize_mode=parent_params.get("resize_mode", "crop_to_new"),
        upscale=parent_params.get("upscale", False),
        t5_preset=t5_preset,
    )

    params_dict = {
        "prompt": prompt, "negative_prompt": req.negative_prompt,
        "model": model.value, "model_preset": model_preset, "t5_preset": t5_preset,
        "width": width, "height": height, "num_frames": num_frames,
        "fps": fps, "steps": steps, "cfg": cfg, "shift": shift,
        "seed": seed, "scheduler": scheduler,
        "loras": [l.model_dump() for l in loras_raw],
        "image_filename": comfy_filename,
        "noise_aug_strength": req.noise_aug_strength,
        "parent_task_id": req.parent_task_id,
        "concat_with_parent": req.concat_with_parent,
    }
    if loras_raw:
        params_dict["final_prompt"] = _inject_trigger_words(prompt, loras_raw)
    params_dict.update(params_extra)

    task_id = await task_manager.create_task(GenerateMode.EXTEND, model, workflow, params=params_dict)
    return GenerateResponse(task_id=task_id, status=TaskStatus.QUEUED)


@router.post("/generate/chain", response_model=ChainResponse)
async def generate_chain(
    image: UploadFile = File(None),
    face_image: UploadFile = File(None),
    initial_reference_image: UploadFile = File(None),
    params: str = Form(...),
    _=Depends(verify_api_key),
):
    """Auto chain generation: split prompt into segments, generate sequentially, concat."""
    from api.main import task_manager
    from api.services.lora_selector import LoraSelector

    try:
        req = AutoChainRequest(**json.loads(params))
    except Exception as e:
        raise HTTPException(400, f"Invalid params JSON: {e}")

    # Handle optional starting image
    image_filename = ""
    face_image_filename = ""
    initial_ref_filename = ""

    logger.info("generate_chain: image_mode=%s image=%s face_image=%s",
                req.image_mode, bool(image and image.filename), bool(face_image and face_image.filename))

    if image:
        image_data = await image.read()
        if image_data:
            local_name, _ = await storage.save_upload(image_data, image.filename or "upload.png")
            client = task_manager.clients.get(req.model.value)
            if client and await client.is_alive():
                upload_result = await client.upload_image(image_data, local_name)
                image_filename = upload_result.get("name", local_name)

    # Handle face reference image for face swap mode
    if face_image:
        face_data = await face_image.read()
        if face_data:
            local_name, _ = await storage.save_upload(face_data, face_image.filename or "face.png")
            client = task_manager.clients.get(req.model.value)
            if client and await client.is_alive():
                upload_result = await client.upload_image(face_data, local_name)
                face_image_filename = upload_result.get("name", local_name)

    # Handle Story Mode continuation from parent chain/video
    parent_video_comfy_filename = ""
    if req.parent_chain_id or req.parent_video_url:
        from api.services.ffmpeg_utils import extract_first_frame

        # Get parent video URL
        parent_video_url = req.parent_video_url
        if req.parent_chain_id and not parent_video_url:
            parent_chain = await task_manager.redis.hgetall(f"chain:{req.parent_chain_id}")
            parent_video_url = parent_chain.get("final_video_url")
            if not parent_video_url:
                raise HTTPException(400, f"Parent chain {req.parent_chain_id} has no video output")

        if not parent_video_url:
            raise HTTPException(400, "parent_video_url is required when parent_chain_id has no video")

        # Download parent video
        parent_video_path = await storage.get_video_path_from_url(parent_video_url)
        if not parent_video_path or not parent_video_path.exists():
            raise HTTPException(400, "Parent video file not found")

        # Extract last N frames as short video for motion reference
        from api.services.ffmpeg_utils import extract_last_n_frames_video
        motion_frames = req.motion_frames if req.motion_frames else 5
        fps = req.fps if req.fps else 16
        short_video_path = await extract_last_n_frames_video(parent_video_path, motion_frames, fps)

        client = task_manager.clients.get(req.model.value)
        if client and await client.is_alive():
            video_data = short_video_path.read_bytes()
            upload_result = await client.upload_video(video_data, short_video_path.name)
            parent_video_comfy_filename = upload_result.get("name", short_video_path.name)
            logger.info("Uploaded parent video (last %d frames) to ComfyUI: %s", motion_frames, parent_video_comfy_filename)

        # Extract first frame as initial reference (for identity consistency)
        if req.story_mode and not initial_reference_image:
            # Check if user provided initial_reference_url
            if req.initial_reference_url:
                initial_ref_path = await storage.get_video_path_from_url(req.initial_reference_url)
                if initial_ref_path and initial_ref_path.exists():
                    initial_ref_data = initial_ref_path.read_bytes()
                else:
                    raise HTTPException(400, "Initial reference image not found")
            else:
                # Extract first frame from parent video
                first_frame_path = await extract_first_frame(parent_video_path)
                initial_ref_data = first_frame_path.read_bytes()

            if client and await client.is_alive():
                local_name = f"initial_ref_{req.parent_chain_id or 'parent'}.png"
                upload_result = await client.upload_image(initial_ref_data, local_name)
                initial_ref_filename = upload_result.get("name", local_name)

    # Handle optional initial reference image upload (for Story Mode identity consistency)
    if initial_reference_image:
        initial_ref_data = await initial_reference_image.read()
        if initial_ref_data:
            local_name, _ = await storage.save_upload(initial_ref_data, initial_reference_image.filename or "initial_ref.png")
            client = task_manager.clients.get(req.model.value)
            if client and await client.is_alive():
                upload_result = await client.upload_image(initial_ref_data, local_name)
                initial_ref_filename = upload_result.get("name", local_name)

    # New format: use segments array if provided
    if req.segments:
        num_segments = len(req.segments)
        segment_prompts = [seg.prompt for seg in req.segments]
        segment_durations = [seg.duration for seg in req.segments]
        segment_loras = [seg.loras for seg in req.segments]
        total_duration = sum(segment_durations)

        # Build segment configs
        segments = []
        for i, seg_req in enumerate(req.segments):
            frames_per_seg = _duration_to_frames(seg_req.duration, req.fps)

            # Merge global loras with segment-specific loras
            all_loras = list(req.loras) + list(seg_req.loras)
            lora_dicts = [l.model_dump() for l in all_loras]

            seg = {
                "prompt": seg_req.prompt,
                "negative_prompt": req.negative_prompt,
                "model": req.model.value,
                "model_preset": req.model_preset,
                "width": req.width, "height": req.height,
                "num_frames": frames_per_seg, "fps": req.fps,
                "steps": req.steps, "cfg": req.cfg, "shift": req.shift,
                "seed": req.seed, "loras": lora_dicts,
                "scheduler": req.scheduler,
                "noise_aug_strength": req.noise_aug_strength,
                "motion_amplitude": req.motion_amplitude,
                "color_match": req.color_match,
                "color_match_method": req.color_match_method,
                "resize_mode": req.resize_mode,
                "upscale": req.upscale,
                "t5_preset": req.t5_preset,
                "original_prompt": seg_req.prompt,
                "auto_continue": req.auto_continue,
                "transition": req.transition,
                "story_mode": True,
                "motion_frames": req.motion_frames,
                "image_mode": req.image_mode.value,
                "face_swap_strength": req.face_swap_strength,
                "boundary": req.boundary,
                "clip_preset": req.clip_preset,
                "match_image_ratio": req.match_image_ratio,
                "enable_upscale": req.enable_upscale,
                "upscale_model": req.upscale_model,
                "upscale_resize": req.upscale_resize,
                "enable_interpolation": req.enable_interpolation,
                "interpolation_multiplier": req.interpolation_multiplier,
                "interpolation_profile": req.interpolation_profile,
                "enable_mmaudio": req.enable_mmaudio,
                "mmaudio_prompt": req.mmaudio_prompt,
                "mmaudio_negative_prompt": req.mmaudio_negative_prompt,
                "mmaudio_steps": req.mmaudio_steps,
                "mmaudio_cfg": req.mmaudio_cfg,
            }
            # Handle image mode for segment 0
            if i == 0:
                if req.image_mode == "first_frame" and image_filename:
                    seg["image_filename"] = image_filename
                elif req.image_mode == "face_reference":
                    # Use face_image if provided, otherwise use image as face reference
                    ref = face_image_filename or image_filename
                    if ref:
                        seg["face_image_filename"] = ref
                        # Add face_swap config for T2V workflow
                        seg["face_swap"] = {
                            "enabled": True,
                            "strength": req.face_swap_strength,
                        }
                    logger.info("Chain seg0 face_reference: face_image=%s image=%s -> face_image_filename=%s",
                                face_image_filename, image_filename, ref)
            # Add parent video filename for Story Mode continuation (multi-frame reference)
            if i == 0 and parent_video_comfy_filename:
                seg["parent_video_filename"] = parent_video_comfy_filename
            # Add initial reference image for Story Mode (all segments)
            if initial_ref_filename:
                seg["initial_ref_filename"] = initial_ref_filename
            segments.append(seg)

        chain_id = await task_manager.create_chain(num_segments, {
            "prompt": " | ".join(segment_prompts[:3]) + ("..." if len(segment_prompts) > 3 else ""),
            "segment_prompts": segment_prompts,
            "segment_durations": segment_durations,
            "total_duration": total_duration,
            "num_segments": num_segments,
            "story_mode": True,
        })
        await task_manager.run_chain(chain_id, segments)

        return ChainResponse(
            chain_id=chain_id,
            total_segments=num_segments,
            status="queued",
        )

    # Legacy format: single prompt with total_duration/segment_duration
    if not req.prompt or not req.total_duration:
        raise HTTPException(400, "Either 'segments' or 'prompt' + 'total_duration' must be provided")

    num_segments = max(1, math.ceil(req.total_duration / req.segment_duration))
    frames_per_seg = _duration_to_frames(req.segment_duration, req.fps)

    # Auto LoRA
    loras = list(req.loras)
    ai_loras = []
    if req.auto_lora:
        selector = LoraSelector()
        ai_loras = await selector.select(req.prompt)
        manual_names = {l.name for l in loras}
        for l in ai_loras:
            if l.name not in manual_names:
                loras.append(l)

    # Auto prompt optimization
    original_prompt = req.prompt
    prompt = req.prompt
    if req.auto_prompt:
        from api.routes.generate import _optimize_prompt
        optimized = await _optimize_prompt(prompt, loras, "t2v", int(req.total_duration))
        if optimized:
            prompt = optimized

    # Split prompt into per-segment prompts
    segment_prompts = split_prompt_by_segments(prompt, req.total_duration, req.segment_duration)

    # Build segment configs
    lora_dicts = [l.model_dump() for l in loras]
    segments = []
    for i in range(num_segments):
        seg = {
            "prompt": segment_prompts[i] if i < len(segment_prompts) else segment_prompts[-1],
            "negative_prompt": req.negative_prompt,
            "model": req.model.value,
            "model_preset": req.model_preset,
            "width": req.width, "height": req.height,
            "num_frames": frames_per_seg, "fps": req.fps,
            "steps": req.steps, "cfg": req.cfg, "shift": req.shift,
            "seed": req.seed, "loras": lora_dicts,
            "scheduler": req.scheduler,
            "noise_aug_strength": req.noise_aug_strength,
            "motion_amplitude": req.motion_amplitude,
            "color_match": req.color_match,
            "color_match_method": req.color_match_method,
            "resize_mode": req.resize_mode,
            "upscale": req.upscale,
            "t5_preset": req.t5_preset,
            "original_prompt": prompt,
            "auto_continue": req.auto_continue,
            "transition": req.transition,
            "story_mode": True,
            "motion_frames": req.motion_frames,
            "image_mode": req.image_mode.value,
            "face_swap_strength": req.face_swap_strength,
            "boundary": req.boundary,
            "clip_preset": req.clip_preset,
            "match_image_ratio": req.match_image_ratio,
            "enable_upscale": req.enable_upscale,
            "upscale_model": req.upscale_model,
            "upscale_resize": req.upscale_resize,
            "enable_interpolation": req.enable_interpolation,
            "interpolation_multiplier": req.interpolation_multiplier,
            "interpolation_profile": req.interpolation_profile,
            "enable_mmaudio": req.enable_mmaudio,
            "mmaudio_prompt": req.mmaudio_prompt,
            "mmaudio_negative_prompt": req.mmaudio_negative_prompt,
            "mmaudio_steps": req.mmaudio_steps,
            "mmaudio_cfg": req.mmaudio_cfg,
        }
        # Handle image mode for segment 0
        if i == 0:
            if req.image_mode == "first_frame" and image_filename:
                seg["image_filename"] = image_filename
            elif req.image_mode == "face_reference":
                ref = face_image_filename or image_filename
                if ref:
                    seg["face_image_filename"] = ref
                logger.info("Single-seg face_reference: face_image=%s image=%s -> face_image_filename=%s",
                            face_image_filename, image_filename, ref)
        if i == 0 and parent_video_comfy_filename:
            seg["parent_video_filename"] = parent_video_comfy_filename
        segments.append(seg)

    chain_id = await task_manager.create_chain(num_segments, {
        "prompt": prompt,
        "original_prompt": original_prompt,
        "ai_prompt": prompt if req.auto_prompt and prompt != original_prompt else None,
        "ai_loras": [l.model_dump() for l in ai_loras] if ai_loras else [],
        "segment_prompts": segment_prompts,
        "total_duration": req.total_duration,
        "segment_duration": req.segment_duration,
        "num_segments": num_segments,
        "story_mode": True,
    })
    await task_manager.run_chain(chain_id, segments)

    return ChainResponse(
        chain_id=chain_id,
        total_segments=num_segments,
        status="queued",
    )


@router.post("/generate/merge-segments")
async def merge_segments(
    segment_task_ids: list[str] = Body(..., embed=True),
    _=Depends(verify_api_key)
):
    """Merge multiple segment videos into one."""
    from api.main import task_manager
    from api.services.ffmpeg_utils import concat_videos
    from api.services import storage
    from api.config import VIDEO_BASE_URL, COS_ENABLED

    if not segment_task_ids or len(segment_task_ids) < 2:
        raise HTTPException(400, "At least 2 segments required for merging")

    # Get all segment tasks and validate
    video_paths = []
    for task_id in segment_task_ids:
        task = await task_manager.get_task(task_id)
        if not task:
            raise HTTPException(404, f"Task {task_id} not found")
        if task["status"] != "completed":
            raise HTTPException(400, f"Task {task_id} is not completed")
        if not task.get("video_url"):
            raise HTTPException(400, f"Task {task_id} has no video output")

        video_path = await storage.get_video_path_from_url(task["video_url"])
        if not video_path or not video_path.exists():
            raise HTTPException(400, f"Video file for task {task_id} not found")
        video_paths.append(video_path)

    # Get FPS from first task
    first_task = await task_manager.get_task(segment_task_ids[0])
    fps = first_task.get("params", {}).get("fps", 24)

    # Concatenate videos
    try:
        merged_path = await concat_videos(video_paths, fps=fps, transition="none")
    except Exception as e:
        raise HTTPException(500, f"Video merge failed: {e}")

    # Save merged video
    merged_data = merged_path.read_bytes()
    result = await storage.save_video(merged_data, "mp4")
    video_url = result if COS_ENABLED else f"{VIDEO_BASE_URL}/{result}"

    # Clean up temporary merged file if it's not in the videos directory
    if merged_path.parent != storage.VIDEOS_DIR:
        merged_path.unlink(missing_ok=True)

    return {
        "video_url": video_url,
        "segment_count": len(segment_task_ids),
        "message": f"Successfully merged {len(segment_task_ids)} segments"
    }


@router.get("/chains", response_model=list[ChainResponse])
async def list_chains(_=Depends(verify_api_key)):
    """List all chain generations."""
    from api.main import task_manager
    chains = await task_manager.list_chains()
    return [ChainResponse(**c) for c in chains]


@router.get("/chains/{chain_id}", response_model=ChainResponse)
async def get_chain_status(chain_id: str, _=Depends(verify_api_key)):
    """Query chain generation status."""
    from api.main import task_manager

    chain = await task_manager.get_chain(chain_id)
    if not chain:
        raise HTTPException(404, "Chain not found")
    return ChainResponse(**chain)


@router.post("/chains/{chain_id}/cancel")
async def cancel_chain(chain_id: str, _=Depends(verify_api_key)):
    """Cancel a running chain."""
    from api.main import task_manager

    ok = await task_manager.cancel_chain(chain_id)
    if not ok:
        raise HTTPException(400, "Chain cannot be cancelled")
    return {"status": "cancelled", "chain_id": chain_id}
