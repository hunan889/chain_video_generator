import logging
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from api.models.schemas import GenerateResponse, GenerateI2VRequest, LoraInput
from api.models.enums import GenerateMode, ModelType, TaskStatus
from api.middleware.auth import verify_api_key
from api.services.workflow_builder import build_workflow, _inject_trigger_words
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
    face_image: UploadFile = File(None),
    _=Depends(verify_api_key),
):
    from api.main import task_manager
    import uuid
    from pathlib import Path
    from api.config import UPLOADS_DIR

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
    local_name, _ = await storage.save_upload(image_data, image.filename or "upload.png")

    # Upload to ComfyUI
    client = next((c for k, c in task_manager.clients.items() if k.startswith(req.model.value + "#")), None)
    if not client or not await client.is_alive():
        raise HTTPException(503, f"ComfyUI {req.model.value} instance is not available")

    upload_result = await client.upload_image(image_data, local_name)
    comfy_filename = upload_result.get("name", local_name)

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
        t5_preset=req.t5_preset,
        face_swap_config=req.face_swap,
        face_image_path=comfy_face_filename,
    )

    params_dict = req.model_dump()
    params_dict["image_filename"] = comfy_filename
    # Store final prompt with trigger keywords for display
    if req.loras:
        params_dict["final_prompt"] = _inject_trigger_words(req.prompt, req.loras)
    if face_image_path:
        params_dict["face_image"] = str(face_image_path)
        params_dict["comfy_face_filename"] = comfy_face_filename
    params_dict.update(params_extra)
    task_id = await task_manager.create_task(GenerateMode.I2V, req.model, workflow, params=params_dict)
    return GenerateResponse(task_id=task_id, status=TaskStatus.QUEUED)
