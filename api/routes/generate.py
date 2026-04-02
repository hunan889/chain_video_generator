import logging
import random
import json
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form, Body, Request
from pydantic import ValidationError
from api.models.schemas import GenerateRequest, GenerateResponse, LoraInput
from api.models.enums import GenerateMode, TaskStatus
from api.middleware.auth import verify_api_key
from api.services.workflow_builder import build_workflow, _inject_trigger_words
from api.services.lora_selector import LoraSelector
from api.config import UPLOADS_DIR

logger = logging.getLogger(__name__)
router = APIRouter()
_lora_selector = LoraSelector()


async def _optimize_prompt(
    prompt: str, loras: list[LoraInput], mode: str, duration: int
) -> str | None:
    """Run prompt optimization, return optimized text or None on failure."""
    from api.config import LLM_API_KEY
    if not LLM_API_KEY:
        return None
    try:
        from api.routes.prompt import _load_lora_context, _get_optimizer
        lora_names = [l.name for l in loras]
        trigger_words, lora_info = _load_lora_context(lora_names)
        result = await _get_optimizer().optimize(
            prompt, trigger_words, mode,
            duration=duration, lora_info=lora_info,
        )
        return result.get("optimized_prompt")
    except Exception as exc:
        logger.warning("Auto prompt optimization failed: %s", exc)
        return None


@router.post("/generate", response_model=GenerateResponse)
async def generate_t2v(
    request: Request,
    _=Depends(verify_api_key)
):
    """
    Generate T2V video. Supports both JSON and FormData:
    - JSON: standard GenerateRequest body
    - FormData: params (JSON string) + optional face_image file
    """
    from api.main import task_manager
    import uuid
    from pathlib import Path

    # Parse request based on Content-Type
    content_type = request.headers.get("content-type", "")
    face_image = None

    if "application/json" in content_type:
        # JSON body
        body = await request.json()
        try:
            req = GenerateRequest(**body)
        except ValidationError as e:
            raise HTTPException(422, detail=e.errors())
    elif "multipart/form-data" in content_type:
        # FormData
        form = await request.form()
        params = form.get("params")
        if not params:
            raise HTTPException(400, "Missing params in FormData")
        try:
            req = GenerateRequest.model_validate_json(params)
        except ValidationError as e:
            raise HTTPException(422, detail=e.errors())
        face_image = form.get("face_image")
    else:
        raise HTTPException(400, "Unsupported Content-Type. Use application/json or multipart/form-data")

    params_extra = {}

    # 1. Auto LoRA: merge AI recommendations with manual selections (manual wins)
    if req.auto_lora:
        ai_loras = await _lora_selector.select(req.prompt)
        manual_names = {l.name for l in req.loras}
        for l in ai_loras:
            if l.name not in manual_names:
                req.loras.append(l)
        params_extra["ai_loras"] = [l.model_dump() for l in ai_loras]

    # 2. Auto Prompt: optimize using final LoRA list
    if req.auto_prompt:
        original_prompt = req.prompt
        fps = req.fps or 24
        duration = int(req.num_frames / fps) if req.num_frames else 3
        optimized = await _optimize_prompt(req.prompt, req.loras, "t2v", duration)
        if optimized:
            req.prompt = optimized
        params_extra["ai_prompt"] = req.prompt
        params_extra["original_prompt"] = original_prompt

    # Check if ComfyUI instance is alive
    client = task_manager._get_client(req.model.value)
    if not client or not await client.is_alive():
        raise HTTPException(503, f"ComfyUI {req.model.value} instance is not available")

    # Save and upload face image if provided
    face_image_path = None
    comfy_face_filename = None
    if face_image and req.face_swap and req.face_swap.enabled:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        face_image_path = UPLOADS_DIR / f"face_{uuid.uuid4().hex}.png"
        face_data = await face_image.read()
        with open(face_image_path, "wb") as f:
            f.write(face_data)
        logger.info(f"Saved face image to {face_image_path}")

        # Upload to ComfyUI
        upload_result = await client.upload_image(face_data, face_image_path.name)
        comfy_face_filename = upload_result.get("name", face_image_path.name)
        logger.info(f"Uploaded face image to ComfyUI as {comfy_face_filename}")

    workflow = build_workflow(
        mode=GenerateMode.T2V,
        model=req.model,
        prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        width=req.width,
        height=req.height,
        num_frames=req.num_frames,
        fps=req.fps,
        steps=req.steps,
        cfg=req.cfg,
        shift=req.shift,
        seed=req.seed,
        loras=req.loras,
        scheduler=req.scheduler,
        model_preset=req.model_preset,
        upscale=req.upscale,
        t5_preset=req.t5_preset,
        face_swap_config=req.face_swap,
        face_image_path=comfy_face_filename,
    )

    params_dict = req.model_dump()
    # Store final prompt with trigger keywords for display
    if req.loras:
        params_dict["final_prompt"] = _inject_trigger_words(req.prompt, req.loras)
    if face_image_path:
        params_dict["face_image"] = str(face_image_path)
        params_dict["comfy_face_filename"] = comfy_face_filename
    params_dict.update(params_extra)
    task_id = await task_manager.create_task(GenerateMode.T2V, req.model, workflow, params=params_dict)
    return GenerateResponse(task_id=task_id, status=TaskStatus.QUEUED)
