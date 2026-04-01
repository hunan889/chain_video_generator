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
async def list_loras(
    config: GatewayConfig = Depends(get_config),
):
    """List all LoRAs from MySQL lora_metadata table."""
    import asyncio
    import pymysql
    import pymysql.cursors

    def _query():
        conn = pymysql.connect(
            host=config.mysql_host, port=config.mysql_port,
            user=config.mysql_user, password=config.mysql_password,
            database=config.mysql_db, charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, file, preview_url, civitai_id, "
                    "trigger_words, trigger_prompt, mode, noise_stage "
                    "FROM lora_metadata WHERE enabled = 1 ORDER BY name"
                )
                return cur.fetchall()
        finally:
            conn.close()

    try:
        rows = await asyncio.to_thread(_query)
        loras = []
        for r in rows:
            tw = r.get("trigger_words") or "[]"
            if isinstance(tw, str):
                try:
                    tw = json.loads(tw)
                except (json.JSONDecodeError, TypeError):
                    tw = []
            loras.append({
                "id": r["id"],
                "name": r.get("name") or r.get("file", ""),
                "filename": r.get("file", ""),
                "preview_url": r.get("preview_url"),
                "civitai_id": r.get("civitai_id"),
                "trigger_words": tw,
                "trigger_prompt": r.get("trigger_prompt"),
                "mode": r.get("mode", "both"),
                "noise_stage": r.get("noise_stage", "high"),
            })
        return {"loras": loras}
    except Exception as e:
        logger.exception("Failed to list LoRAs from MySQL")
        return {"loras": []}


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
