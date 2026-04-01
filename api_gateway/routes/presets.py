"""Model and T5 preset endpoints."""

from fastapi import APIRouter
from shared.workflow_builder import get_model_presets, get_t5_presets

router = APIRouter(prefix="/api/v1", tags=["presets"])


@router.get("/model-presets")
async def model_presets():
    """Return available model presets."""
    return get_model_presets()


@router.get("/t5-presets")
async def t5_presets():
    """Return available T5 presets."""
    return get_t5_presets()
