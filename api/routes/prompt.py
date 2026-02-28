import logging
import yaml
from fastapi import APIRouter, Depends, HTTPException
from api.models.schemas import PromptOptimizeRequest, PromptOptimizeResponse
from api.services.prompt_optimizer import PromptOptimizer
from api.config import LLM_API_KEY, LORAS_PATH
from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()

_optimizer: PromptOptimizer | None = None


def _get_optimizer() -> PromptOptimizer:
    global _optimizer
    if _optimizer is None:
        _optimizer = PromptOptimizer()
    return _optimizer


def _load_lora_context(lora_names: list[str]) -> tuple[list[str], list[dict]]:
    """Load trigger words and full context for the given LoRA names."""
    if not lora_names:
        return [], []
    try:
        with open(LORAS_PATH) as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return [], []
    lora_map = {
        item["name"]: item
        for item in data.get("loras", [])
        if "name" in item
    }
    words = []
    lora_info = []
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
        })
    return words, lora_info


@router.post("/prompt/optimize", response_model=PromptOptimizeResponse)
async def optimize_prompt(req: PromptOptimizeRequest, _=Depends(verify_api_key)):
    if not LLM_API_KEY:
        raise HTTPException(501, "LLM_API_KEY not configured")

    trigger_words, lora_info = _load_lora_context(req.lora_names)

    try:
        result = await _get_optimizer().optimize(
            req.prompt, trigger_words, req.mode,
            image_base64=req.image_base64,
            duration=req.duration,
            lora_info=lora_info,
        )
    except Exception as e:
        raise HTTPException(502, f"Prompt optimization failed: {e}")

    return PromptOptimizeResponse(
        original_prompt=req.prompt,
        optimized_prompt=result["optimized_prompt"],
        trigger_words_used=trigger_words,
        explanation=result.get("explanation", ""),
    )
