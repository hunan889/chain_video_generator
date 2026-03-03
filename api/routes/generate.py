import logging
import random
from fastapi import APIRouter, Depends, HTTPException
from api.models.schemas import GenerateRequest, GenerateResponse, LoraInput
from api.models.enums import GenerateMode, TaskStatus
from api.middleware.auth import verify_api_key
from api.services.workflow_builder import build_workflow, _inject_trigger_words
from api.services.lora_selector import LoraSelector

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
async def generate_t2v(req: GenerateRequest, _=Depends(verify_api_key)):
    from api.main import task_manager

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
        t5_preset=req.t5_preset,
    )

    params_dict = req.model_dump()
    # Store final prompt with trigger keywords for display
    if req.loras:
        params_dict["final_prompt"] = _inject_trigger_words(req.prompt, req.loras)
    params_dict.update(params_extra)
    task_id = await task_manager.create_task(GenerateMode.T2V, req.model, workflow, params=params_dict)
    return GenerateResponse(task_id=task_id, status=TaskStatus.QUEUED)
