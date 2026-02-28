import logging
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from api.models.schemas import GenerateResponse, GenerateI2VRequest, LoraInput
from api.models.enums import GenerateMode, ModelType, TaskStatus
from api.middleware.auth import verify_api_key
from api.services.workflow_builder import build_workflow
from api.services.lora_selector import LoraSelector
from api.services import storage
import json

logger = logging.getLogger(__name__)
router = APIRouter()
_lora_selector = LoraSelector()


@router.post("/generate/i2v", response_model=GenerateResponse)
async def generate_i2v(
    image: UploadFile = File(...),
    params: str = Form(...),
    _=Depends(verify_api_key),
):
    from api.main import task_manager

    try:
        req = GenerateI2VRequest(**json.loads(params))
    except Exception as e:
        raise HTTPException(400, f"Invalid params JSON: {e}")

    params_extra = {}

    # 1. Auto LoRA: merge AI recommendations with manual selections (manual wins)
    if req.auto_lora:
        ai_loras = await _lora_selector.select(req.prompt)
        manual_names = {l.name for l in req.loras}
        for l in ai_loras:
            if l.name not in manual_names:
                req.loras.append(l)
        params_extra["ai_loras"] = [l.model_dump() for l in ai_loras]

    # 2. Auto Prompt: optimize using final LoRA list (no image_base64 in params)
    if req.auto_prompt:
        from api.routes.generate import _optimize_prompt
        original_prompt = req.prompt
        fps = req.fps or 24
        duration = int(req.num_frames / fps) if req.num_frames else 3
        optimized = await _optimize_prompt(req.prompt, req.loras, "i2v", duration)
        if optimized:
            req.prompt = optimized
        params_extra["ai_prompt"] = req.prompt
        params_extra["original_prompt"] = original_prompt

    # Save uploaded image
    image_data = await image.read()
    if not image_data:
        raise HTTPException(400, "Empty image file")
    local_name = storage.save_upload(image_data, image.filename or "upload.png")

    # Upload to ComfyUI
    client = task_manager.clients.get(req.model.value)
    if not client or not await client.is_alive():
        raise HTTPException(503, f"ComfyUI {req.model.value} instance is not available")

    upload_result = await client.upload_image(image_data, local_name)
    comfy_filename = upload_result.get("name", local_name)

    workflow = build_workflow(
        mode=GenerateMode.I2V,
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
        image_filename=comfy_filename,
        noise_aug_strength=req.noise_aug_strength,
        model_preset=req.model_preset,
        motion_amplitude=req.motion_amplitude,
        color_match=req.color_match,
        color_match_method=req.color_match_method,
        resize_mode=req.resize_mode,
        upscale=req.upscale,
    )

    params_dict = req.model_dump()
    params_dict["image_filename"] = comfy_filename
    params_dict.update(params_extra)
    task_id = await task_manager.create_task(GenerateMode.I2V, req.model, workflow, params=params_dict)
    return GenerateResponse(task_id=task_id, status=TaskStatus.QUEUED)
