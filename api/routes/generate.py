import random
from fastapi import APIRouter, Depends, HTTPException
from api.models.schemas import GenerateRequest, GenerateResponse
from api.models.enums import GenerateMode, TaskStatus
from api.middleware.auth import verify_api_key
from api.services.workflow_builder import build_workflow

router = APIRouter()


@router.post("/generate", response_model=GenerateResponse)
async def generate_t2v(req: GenerateRequest, _=Depends(verify_api_key)):
    from api.main import task_manager

    # Check if ComfyUI instance is alive
    client = task_manager.clients.get(req.model.value)
    if not client or not await client.is_alive():
        raise HTTPException(503, f"ComfyUI {req.model.value} instance is not available")

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
    )

    task_id = await task_manager.create_task(GenerateMode.T2V, req.model, workflow, params=req.model_dump())
    return GenerateResponse(task_id=task_id, status=TaskStatus.QUEUED)
