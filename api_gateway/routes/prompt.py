"""Prompt optimization and image description routes.

Ports /api/v1/prompt/optimize, /api/v1/prompt/describe-image, and
/api/v1/prompt/continuation from the old monolith to the new gateway.
"""

import logging
import yaml
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel, Field

from api_gateway.config import GatewayConfig
from api_gateway.dependencies import get_config
from api_gateway.services.prompt_optimizer import make_prompt_optimizer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["prompt"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class PromptOptimizeRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    lora_names: list[str] = Field(default_factory=list)
    mode: str = Field(default="i2v", pattern="^(t2v|i2v)$")
    image_base64: Optional[str] = Field(default=None)
    duration: float = Field(default=3.3, ge=0.5, le=10)


class PromptOptimizeResponse(BaseModel):
    original_prompt: str
    optimized_prompt: str
    trigger_words_used: list[str] = Field(default_factory=list)
    explanation: str = ""


class ContinuationRequest(BaseModel):
    user_intent: str
    previous_video_prompt: str
    frame_image_base64: Optional[str] = None
    duration: float = 3.0
    continuation_index: int = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_lora_context(lora_names: list[str], loras_yaml_path: str) -> tuple[list[str], list[dict]]:
    """Load trigger words and full context for the given LoRA names from YAML."""
    if not lora_names or not loras_yaml_path:
        return [], []
    try:
        with open(loras_yaml_path) as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return [], []
    lora_map = {item["name"]: item for item in data.get("loras", []) if "name" in item}
    words: list[str] = []
    lora_info: list[dict] = []
    for name in lora_names:
        item = lora_map.get(name)
        if not item:
            continue
        for w in item.get("trigger_words", []):
            if w not in words:
                words.append(w)
        lora_info.append({
            "name": item["name"],
            "description": item.get("description", ""),
            "trigger_words": item.get("trigger_words", []),
            "example_prompts": item.get("example_prompts", []),
        })
    return words, lora_info


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/prompt/optimize", response_model=PromptOptimizeResponse)
async def optimize_prompt(
    req: PromptOptimizeRequest,
    config: GatewayConfig = Depends(get_config),
):
    """Optimize a video generation prompt using LLM."""
    if not config.llm_api_key:
        raise HTTPException(501, "LLM_API_KEY not configured")

    trigger_words, lora_info = _load_lora_context(req.lora_names, config.loras_yaml_path)

    try:
        optimizer = make_prompt_optimizer(
            llm_api_key=config.llm_api_key,
            llm_base_url=config.llm_base_url,
            llm_model=config.llm_model,
            vision_api_key=config.vision_api_key,
            vision_base_url=config.vision_base_url,
            vision_model=config.vision_model,
        )
        result = await optimizer.optimize(
            req.prompt, trigger_words, req.mode,
            image_base64=req.image_base64,
            duration=req.duration,
            lora_info=lora_info,
        )
    except Exception as e:
        logger.exception("Prompt optimization failed")
        raise HTTPException(502, f"Prompt optimization failed: {e}")

    optimized = result["optimized_prompt"]
    if trigger_words:
        prompt_lower = optimized.lower()
        missing = [w for w in trigger_words if w.lower() not in prompt_lower]
        if missing:
            optimized = "\n".join(missing) + "\n\n" + optimized

    return PromptOptimizeResponse(
        original_prompt=req.prompt,
        optimized_prompt=optimized,
        trigger_words_used=trigger_words,
        explanation=result.get("explanation", ""),
    )


@router.post("/prompt/describe-image")
async def describe_image(
    image_base64: str = Body(..., embed=True),
    config: GatewayConfig = Depends(get_config),
):
    """Describe an image using the vision model."""
    if not config.vision_api_key:
        raise HTTPException(501, "VISION_API_KEY not configured")

    try:
        optimizer = make_prompt_optimizer(
            llm_api_key=config.llm_api_key,
            llm_base_url=config.llm_base_url,
            llm_model=config.llm_model,
            vision_api_key=config.vision_api_key,
            vision_base_url=config.vision_base_url,
            vision_model=config.vision_model,
        )
        description = await optimizer._describe_image(image_base64)
        return {"description": description}
    except Exception as e:
        logger.exception("Image description failed")
        raise HTTPException(502, f"Image description failed: {e}")


@router.post("/prompt/continuation")
async def generate_continuation(
    req: ContinuationRequest,
    config: GatewayConfig = Depends(get_config),
):
    """Generate a continuation prompt for the next chain segment."""
    if not config.llm_api_key:
        raise HTTPException(501, "LLM_API_KEY not configured")

    try:
        optimizer = make_prompt_optimizer(
            llm_api_key=config.llm_api_key,
            llm_base_url=config.llm_base_url,
            llm_model=config.llm_model,
            vision_api_key=config.vision_api_key,
            vision_base_url=config.vision_base_url,
            vision_model=config.vision_model,
        )
        result = await optimizer.generate_continuation_prompt(
            user_intent=req.user_intent,
            previous_video_prompt=req.previous_video_prompt,
            frame_image_base64=req.frame_image_base64 or "",
            duration=req.duration,
            continuation_index=req.continuation_index,
        )
        return {"continuation_prompt": result}
    except Exception as e:
        logger.exception("Continuation prompt generation failed")
        raise HTTPException(502, f"Continuation prompt generation failed: {e}")
