"""Video extend endpoint.

POST /api/v1/generate/extend -- extend a completed video using its last frame (I2V).
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api_gateway.dependencies import get_gateway
from shared.enums import GenerateMode, ModelType, TaskStatus
from shared.redis_keys import task_key
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/generate", tags=["extend"])


class ExtendRequest(BaseModel):
    parent_task_id: str
    prompt: str = ""
    negative_prompt: str = ""
    num_frames: int = 81
    steps: int = 20
    cfg: float = 6.0
    seed: int = -1
    loras: list = []


@router.post("/extend")
async def extend_video(
    req: ExtendRequest,
    gateway: TaskGateway = Depends(get_gateway),
):
    """Create an I2V task that continues from the last frame of a completed task."""
    parent = await gateway.get_task(req.parent_task_id)
    if parent is None:
        raise HTTPException(status_code=404, detail=f"Task {req.parent_task_id!r} not found")

    if parent.get("status") != TaskStatus.COMPLETED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Task {req.parent_task_id!r} is not completed (status={parent.get('status')})",
        )

    # Fetch raw hash to get last_frame_url (not always in get_task result)
    raw = await gateway.redis.hgetall(task_key(req.parent_task_id))
    last_frame_url = raw.get("last_frame_url", "")
    if not last_frame_url:
        raise HTTPException(
            status_code=400,
            detail=f"Task {req.parent_task_id!r} has no last_frame_url. "
                   "Generate with extract_last_frame=true first.",
        )

    # Inherit generation params from parent
    parent_params = json.loads(raw.get("params", "{}"))
    model_str = raw.get("model", ModelType.A14B.value)
    try:
        model = ModelType(model_str)
    except ValueError:
        model = ModelType.A14B

    width = parent_params.get("width", 832)
    height = parent_params.get("height", 480)
    fps = parent_params.get("fps", 16)

    # Input file: last frame via COS key placeholder
    input_files = [{"cos_url": last_frame_url, "placeholder": "__EXTEND_FRAME__"}]

    workflow = {
        "_meta": {"version": "gateway_v1", "extend": True},
        "prompt": req.prompt,
        "negative_prompt": req.negative_prompt,
        "mode": GenerateMode.I2V.value,
        "model": model.value,
        "width": width,
        "height": height,
        "num_frames": req.num_frames,
        "fps": fps,
        "steps": req.steps,
        "cfg": req.cfg,
        "seed": req.seed,
        "image_filename": "__EXTEND_FRAME__",
        "loras": req.loras,
    }

    params = {
        "prompt": req.prompt,
        "negative_prompt": req.negative_prompt,
        "width": width,
        "height": height,
        "num_frames": req.num_frames,
        "fps": fps,
        "steps": req.steps,
        "cfg": req.cfg,
        "seed": req.seed,
        "parent_task_id": req.parent_task_id,
    }

    task_id = await gateway.create_task(
        mode=GenerateMode.I2V,
        model=model,
        workflow=workflow,
        params=params,
    )

    # Store input_files so the worker can download the last frame
    await gateway.redis.hset(
        task_key(task_id),
        mapping={"input_files": json.dumps(input_files)},
    )

    return {"task_id": task_id, "status": "queued"}
