"""LoRA management endpoints.

GET  /api/v1/loras          -- list all LoRAs published by GPU workers
POST /api/v1/loras/download -- queue a LoRA download task on the GPU worker
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api_gateway.config import GatewayConfig
from api_gateway.dependencies import get_config, get_gateway
from api_gateway.services.lora_selector import LoraSelector
from shared.enums import GenerateMode, ModelType
from shared.redis_keys import WORKER_LORAS_PREFIX
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["loras"])


class LoraDownloadRequest(BaseModel):
    civitai_version_id: int
    filename: str


class LoraRecommendRequest(BaseModel):
    prompt: str


@router.get("/loras")
async def list_loras(gw: TaskGateway = Depends(get_gateway)):
    """Aggregate and deduplicate LoRAs published by all GPU workers."""
    redis = gw.redis
    seen: dict[str, dict] = {}

    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{WORKER_LORAS_PREFIX}:*", count=100)
        for key in keys:
            raw = await redis.get(key)
            if not raw:
                continue
            try:
                loras = json.loads(raw)
            except Exception:
                continue
            for lora in loras:
                name = lora.get("name", "")
                if name and name not in seen:
                    seen[name] = lora
        if cursor == 0:
            break

    return {"loras": sorted(seen.values(), key=lambda l: l["name"])}


@router.post("/loras/recommend")
async def recommend_loras(
    req: LoraRecommendRequest,
    config: GatewayConfig = Depends(get_config),
):
    """AI-powered LoRA recommendation based on prompt."""
    if not config.llm_api_key:
        raise HTTPException(501, "LLM_API_KEY not configured")
    selector = LoraSelector(
        api_key=config.llm_api_key,
        base_url=config.llm_base_url,
        model=config.llm_model,
        loras_yaml_path=config.loras_yaml_path,
    )
    loras = await selector.select(req.prompt)
    return {"loras": [{"name": l.name, "strength": l.strength} for l in loras]}


@router.post("/loras/download")
async def download_lora(
    req: LoraDownloadRequest,
    gw: TaskGateway = Depends(get_gateway),
):
    """Queue a LoRA download task on the GPU worker."""
    workflow = {
        "civitai_version_id": req.civitai_version_id,
        "filename": req.filename,
    }
    task_id = await gw.create_task(
        mode=GenerateMode.LORA_DOWNLOAD,
        model=ModelType.A14B,
        workflow=workflow,
        params={"civitai_version_id": req.civitai_version_id, "filename": req.filename},
    )
    return {"task_id": task_id, "status": "queued"}
