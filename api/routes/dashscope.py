"""
DashScope-compatible API endpoints.

Exposes the advanced workflow via Alibaba Cloud DashScope request/response format,
allowing callers that speak DashScope protocol to submit and query video generation tasks.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

from api.middleware.auth import _load_keys
from api.routes.workflow import (
    WorkflowGenerateRequest,
    generate_advanced_workflow,
    get_workflow_status,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class DashScopeInput(BaseModel):
    prompt: str
    img_url: Optional[str] = None
    negative_prompt: Optional[str] = None


class DashScopeParameters(BaseModel):
    size: Optional[str] = None          # e.g. "1920*1080"
    duration: Optional[int] = None      # seconds
    prompt_extend: Optional[bool] = None
    # Extended parameters (beyond standard DashScope)
    mode: Optional[str] = None          # e.g. "t2v", "first_frame", "face_reference", "full_body_reference"
    resolution: Optional[str] = None    # e.g. "480p", "720p", "1080p"
    aspect_ratio: Optional[str] = None  # e.g. "16:9", "9:16", "3:4"
    turbo: Optional[bool] = None
    mmaudio_enabled: Optional[bool] = None
    mmaudio_prompt: Optional[str] = None


class DashScopeRequest(BaseModel):
    model: str
    input: DashScopeInput
    parameters: Optional[DashScopeParameters] = None


class DashScopeStage(BaseModel):
    name: str
    status: str
    sub_stage: Optional[str] = None


class DashScopeTaskOutput(BaseModel):
    task_id: str
    task_status: str
    video_url: Optional[str] = None
    submit_time: Optional[str] = None
    end_time: Optional[str] = None
    # Extended fields for progress tracking
    progress: Optional[float] = None
    current_stage: Optional[str] = None
    stages: Optional[list[DashScopeStage]] = None
    first_frame_url: Optional[str] = None
    edited_frame_url: Optional[str] = None
    error_message: Optional[str] = None


class DashScopeResponse(BaseModel):
    request_id: str
    output: DashScopeTaskOutput
    usage: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_MAP = {
    "queued": "PENDING",
    "running": "RUNNING",
    "completed": "SUCCEEDED",
    "failed": "FAILED",
}

# Common size → (resolution, aspect_ratio) mapping
_SIZE_MAP = {
    "1920*1080": ("1080p", "16:9"),
    "1080*1920": ("1080p", "9:16"),
    "1280*720":  ("720p", "16:9"),
    "720*1280":  ("720p", "9:16"),
    "854*480":   ("480p", "16:9"),
    "480*854":   ("480p", "9:16"),
    "1024*1024": ("720p", "1:1"),
    "1080*1080": ("1080p", "1:1"),
}


def _parse_size(size: str) -> tuple[Optional[str], Optional[str]]:
    """Parse DashScope size string into (resolution, aspect_ratio)."""
    if size in _SIZE_MAP:
        return _SIZE_MAP[size]
    # Try to figure it out from dimensions
    parts = size.split("*")
    if len(parts) == 2:
        try:
            w, h = int(parts[0]), int(parts[1])
        except ValueError:
            return None, None
        # Determine resolution from larger dimension
        max_dim = max(w, h)
        if max_dim >= 1920:
            resolution = "1080p"
        elif max_dim >= 1280:
            resolution = "720p"
        else:
            resolution = "480p"
        # Determine aspect ratio
        from math import gcd
        g = gcd(w, h)
        aspect_ratio = f"{w // g}:{h // g}"
        return resolution, aspect_ratio
    return None, None


def _verify_key(x_api_key: Optional[str], authorization: Optional[str]):
    """Verify API key from either X-API-Key header or Authorization: Bearer header."""
    api_key = x_api_key
    if not api_key and authorization:
        # Support "Bearer <key>" format
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            api_key = parts[1]
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    keys = _load_keys()
    if api_key not in keys:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return keys[api_key]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/dashscope/video-generation", response_model=DashScopeResponse)
async def dashscope_submit(
    req: DashScopeRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
):
    """Submit a video generation task in DashScope format."""
    _verify_key(x_api_key, authorization)

    model = req.model.lower()
    has_image = bool(req.input.img_url)
    params = req.parameters or DashScopeParameters()

    # --- Determine mode and image fields ---
    if params.mode:
        # Explicit mode override via parameters
        mode = params.mode
        if mode == "first_frame" and has_image:
            uploaded_first_frame = req.input.img_url
            reference_image = None
        elif has_image:
            uploaded_first_frame = None
            reference_image = req.input.img_url
        else:
            uploaded_first_frame = None
            reference_image = None
    elif "i2v" in model and has_image:
        # Image-to-video
        mode = "first_frame"
        uploaded_first_frame = req.input.img_url
        reference_image = None
    elif "t2v" in model and has_image:
        # Text-to-video with face reference
        mode = "face_reference"
        uploaded_first_frame = None
        reference_image = req.input.img_url
    else:
        # Pure text-to-video
        mode = "t2v"
        uploaded_first_frame = None
        reference_image = None

    # --- Parse size / resolution ---
    resolution = params.resolution
    aspect_ratio = params.aspect_ratio
    if params.size and not resolution:
        resolution, aspect_ratio = _parse_size(params.size)

    # --- Build mmaudio config ---
    mmaudio = None
    if params.mmaudio_enabled:
        mmaudio = {"enabled": True, "prompt": params.mmaudio_prompt or ""}

    # --- Build internal request ---
    internal_req = WorkflowGenerateRequest(
        mode=mode,
        user_prompt=req.input.prompt,
        uploaded_first_frame=uploaded_first_frame,
        reference_image=reference_image,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        duration=params.duration,
        auto_prompt=params.prompt_extend if params.prompt_extend is not None else True,
        turbo=params.turbo if params.turbo is not None else False,
        mmaudio=mmaudio,
        first_frame_source=None,  # Deprecated: unified logic in _find_base_image()
    )

    logger.info(f"[DASHSCOPE] Submit: model={req.model}, mode={mode}, has_image={has_image}")

    # Call the real handler
    result = await generate_advanced_workflow(internal_req, None)

    return DashScopeResponse(
        request_id=uuid.uuid4().hex,
        output=DashScopeTaskOutput(
            task_id=result.workflow_id,
            task_status=_STATUS_MAP.get(result.status, "PENDING"),
        ),
    )


@router.get("/dashscope/tasks/{task_id}", response_model=DashScopeResponse)
async def dashscope_query(
    task_id: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
):
    """Query task status in DashScope format."""
    _verify_key(x_api_key, authorization)

    result = await get_workflow_status(task_id, None)

    # Build times from elapsed_time
    submit_time = None
    end_time = None
    if result.elapsed_time is not None:
        import time as _time
        from datetime import datetime, timezone
        # Approximate submit_time from current time - elapsed
        ts_submit = _time.time() - result.elapsed_time
        submit_time = datetime.fromtimestamp(ts_submit, tz=timezone.utc).isoformat()
        if result.status in ("completed", "failed"):
            end_time = datetime.fromtimestamp(_time.time(), tz=timezone.utc).isoformat()

    # Build stages list
    stages = None
    if result.stages:
        stages = [
            DashScopeStage(name=s.name, status=s.status, sub_stage=s.sub_stage)
            for s in result.stages
        ]

    return DashScopeResponse(
        request_id=uuid.uuid4().hex,
        output=DashScopeTaskOutput(
            task_id=task_id,
            task_status=_STATUS_MAP.get(result.status, "UNKNOWN"),
            video_url=result.final_video_url,
            submit_time=submit_time,
            end_time=end_time,
            progress=result.progress,
            current_stage=result.current_stage,
            stages=stages,
            first_frame_url=result.first_frame_url,
            edited_frame_url=result.edited_frame_url,
            error_message=result.error,
        ),
        usage={},
    )
