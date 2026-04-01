"""CivitAI proxy routes for the API Gateway.

Search and model-detail endpoints proxy to CivitAI's API.
Download is queued via Redis LORA_DOWNLOAD task (GPU worker handles the file).
No MySQL, no direct disk writes.
"""

import logging
import os
import sys
import types
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api_gateway.config import GatewayConfig
from api_gateway.dependencies import get_config, get_gateway
from shared.enums import GenerateMode, ModelType
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["civitai"])

# Project root: parent of api_gateway/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CivitAIDownloadRequest(BaseModel):
    civitai_version_id: int
    filename: str


def _ensure_api_stubs(token: str) -> None:
    """Ensure api.config and api.models.schemas stubs exist so civitai_client imports work."""
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

    # Stub api.config (prevent the real one from loading ComfyUI env vars)
    if "api.config" not in sys.modules:
        fake_cfg = types.ModuleType("api.config")
        fake_cfg.CIVITAI_API_TOKEN = token
        sys.modules["api.config"] = fake_cfg
    else:
        sys.modules["api.config"].CIVITAI_API_TOKEN = token

    # Stub api.models.schemas with the dataclasses civitai_client expects
    if "api.models.schemas" not in sys.modules:
        if "api.models" not in sys.modules:
            fake_models = types.ModuleType("api.models")
            fake_models.__path__ = [os.path.join(_PROJECT_ROOT, "api", "models")]
            sys.modules["api.models"] = fake_models

        from dataclasses import dataclass, field

        fake_schemas = types.ModuleType("api.models.schemas")

        @dataclass
        class CivitAIFile:
            name: str = ""
            size_mb: float = 0
            download_url: str = ""

        @dataclass
        class CivitAIModelVersion:
            id: int = 0
            name: str = ""
            trained_words: list = field(default_factory=list)
            download_url: str = ""
            base_model: str = ""
            file_size_mb: float = 0
            files: list = field(default_factory=list)

        @dataclass
        class CivitAIModelResult:
            id: int = 0
            name: str = ""
            description: str = ""
            tags: list = field(default_factory=list)
            preview_url: Optional[str] = None
            versions: list = field(default_factory=list)
            stats: dict = field(default_factory=dict)

        fake_schemas.CivitAIFile = CivitAIFile
        fake_schemas.CivitAIModelVersion = CivitAIModelVersion
        fake_schemas.CivitAIModelResult = CivitAIModelResult
        sys.modules["api.models.schemas"] = fake_schemas


def _civitai_client(token: str):
    """Import civitai_client with stubs so api.config/api.models.schemas resolve."""
    _ensure_api_stubs(token)
    import api.services.civitai_client as _c
    _c.CIVITAI_API_TOKEN = token
    return _c


@router.get("/civitai/search")
async def search_civitai(
    query: str = Query(default="wan 2.1"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str = Query(default=""),
    nsfw: bool = Query(default=True),
    sort: str = Query(default="Most Downloaded"),
    base_model: str = Query(default=""),
    config: GatewayConfig = Depends(get_config),
):
    """Search CivitAI LoRAs (Meilisearch with v1 API fallback)."""
    client = _civitai_client(config.civitai_api_token)
    try:
        return await client.search_loras(
            query=query, limit=limit, cursor=cursor,
            nsfw=nsfw, sort=sort, base_model=base_model,
        )
    except Exception as e:
        logger.exception("CivitAI search failed")
        raise HTTPException(502, f"CivitAI search failed: {e}")


@router.get("/civitai/models/{model_id}")
async def get_civitai_model(
    model_id: int,
    config: GatewayConfig = Depends(get_config),
):
    """Get CivitAI model detail by ID."""
    client = _civitai_client(config.civitai_api_token)
    try:
        return await client.get_model(model_id)
    except Exception as e:
        logger.exception("CivitAI model fetch failed")
        raise HTTPException(502, f"CivitAI model fetch failed: {e}")


@router.post("/civitai/download")
async def download_civitai_lora(
    req: CivitAIDownloadRequest,
    gw: TaskGateway = Depends(get_gateway),
):
    """Queue a LoRA download via Redis task (GPU worker downloads the file)."""
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
