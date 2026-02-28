from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from api.models.schemas import GenerateResponse, GenerateI2VRequest, LoraInput
from api.models.enums import GenerateMode, ModelType, TaskStatus
from api.middleware.auth import verify_api_key
from api.services.workflow_builder import build_workflow
from api.services import storage
import json

router = APIRouter()


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
    task_id = await task_manager.create_task(GenerateMode.I2V, req.model, workflow, params=params_dict)
    return GenerateResponse(task_id=task_id, status=TaskStatus.QUEUED)
