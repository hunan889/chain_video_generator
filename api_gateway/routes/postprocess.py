"""Post-processing endpoints.

POST /api/v1/postprocess/interpolate  -- RIFE frame interpolation
POST /api/v1/postprocess/upscale       -- video upscaling
POST /api/v1/postprocess/audio         -- MMAudio generation
POST /api/v1/postprocess/faceswap      -- ReActor face swap
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api_gateway.dependencies import get_cos_client, get_gateway
from shared.cos.client import COSClient
from shared.enums import GenerateMode, ModelType, TaskStatus
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/postprocess", tags=["postprocess"])


async def _resolve_video_cos_url(
    task_id: Optional[str],
    gateway: TaskGateway,
) -> str:
    """Resolve a completed task's video COS URL."""
    if not task_id:
        raise HTTPException(status_code=422, detail="task_id is required")
    task = await gateway.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    if task.get("status") != TaskStatus.COMPLETED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id!r} is not completed (status={task.get('status')})",
        )
    video_url = task.get("video_url", "")
    if not video_url:
        raise HTTPException(status_code=400, detail=f"Task {task_id!r} has no video_url")
    return video_url


@router.post("/interpolate")
async def interpolate(
    task_id: Optional[str] = Form(None),
    multiplier: int = Form(2),
    fps: float = Form(16.0),
    gateway: TaskGateway = Depends(get_gateway),
    cos_client: COSClient = Depends(get_cos_client),
):
    """Queue a frame-interpolation task."""
    video_cos_url = await _resolve_video_cos_url(task_id, gateway)
    workflow = {
        "source_video": video_cos_url,
        "multiplier": multiplier,
        "fps": fps,
    }
    new_task_id = await gateway.create_task(
        mode=GenerateMode.INTERPOLATE,
        model=ModelType.A14B,
        workflow=workflow,
        params={"source_task_id": task_id, "multiplier": multiplier, "fps": fps},
    )
    return {"task_id": new_task_id, "status": "queued"}


@router.post("/upscale")
async def upscale(
    task_id: Optional[str] = Form(None),
    model: str = Form("4x_foolhardy_Remacri"),
    resize_to: str = Form("2x"),
    gateway: TaskGateway = Depends(get_gateway),
    cos_client: COSClient = Depends(get_cos_client),
):
    """Queue a video-upscale task."""
    video_cos_url = await _resolve_video_cos_url(task_id, gateway)
    workflow = {
        "source_video": video_cos_url,
        "model": model,
        "resize_to": resize_to,
    }
    new_task_id = await gateway.create_task(
        mode=GenerateMode.UPSCALE,
        model=ModelType.A14B,
        workflow=workflow,
        params={"source_task_id": task_id, "upscale_model": model, "resize_to": resize_to},
    )
    return {"task_id": new_task_id, "status": "queued"}


@router.post("/audio")
async def audio(
    task_id: Optional[str] = Form(None),
    prompt: str = Form(""),
    negative_prompt: str = Form(""),
    steps: int = Form(25),
    cfg: float = Form(4.5),
    fps: float = Form(16.0),
    gateway: TaskGateway = Depends(get_gateway),
    cos_client: COSClient = Depends(get_cos_client),
):
    """Queue an audio-generation task."""
    video_cos_url = await _resolve_video_cos_url(task_id, gateway)
    workflow = {
        "source_video": video_cos_url,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "steps": steps,
        "cfg": cfg,
        "fps": fps,
    }
    new_task_id = await gateway.create_task(
        mode=GenerateMode.AUDIO,
        model=ModelType.A14B,
        workflow=workflow,
        params={
            "source_task_id": task_id,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "steps": steps,
            "cfg": cfg,
            "fps": fps,
        },
    )
    return {"task_id": new_task_id, "status": "queued"}


@router.post("/faceswap")
async def faceswap(
    task_id: Optional[str] = Form(None),
    strength: float = Form(1.0),
    face_image: UploadFile = File(...),
    gateway: TaskGateway = Depends(get_gateway),
    cos_client: COSClient = Depends(get_cos_client),
):
    """Queue a face-swap task. face_image is required."""
    video_cos_url = await _resolve_video_cos_url(task_id, gateway)

    # Upload face image to COS
    face_bytes = await face_image.read()
    face_filename = f"{uuid.uuid4().hex}.png"
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(face_bytes)
        tmp_path = tmp.name
    try:
        face_cos_url = cos_client.upload_file(
            local_path=tmp_path,
            subdir="faces",
            filename=face_filename,
        )
    finally:
        os.unlink(tmp_path)

    workflow = {
        "source_video": video_cos_url,
        "face_image": face_cos_url,
        "strength": strength,
    }
    new_task_id = await gateway.create_task(
        mode=GenerateMode.FACESWAP,
        model=ModelType.A14B,
        workflow=workflow,
        params={"source_task_id": task_id, "face_cos_url": face_cos_url, "strength": strength},
    )
    return {"task_id": new_task_id, "status": "queued"}
