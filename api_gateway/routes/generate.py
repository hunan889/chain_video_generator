"""POST /api/v1/generate — submit a video generation task.

Accepts form data (multipart for image uploads) and creates a task in Redis.
If workflow_json is provided, it is used directly. Otherwise a simplified
workflow dict is built from the individual parameters.
"""

import json
import logging
import tempfile
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api_gateway.dependencies import get_cos_client, get_gateway
from shared.cos.client import COSClient
from shared.enums import GenerateMode, ModelType
from shared.redis_keys import task_key
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["generate"])


def _build_simple_workflow(
    *,
    prompt: str,
    negative_prompt: str,
    mode: GenerateMode,
    model: ModelType,
    width: int,
    height: int,
    num_frames: int,
    fps: int,
    steps: int,
    cfg: float,
    shift: float,
    seed: int,
) -> dict:
    """Build a minimal workflow dict.

    This is a temporary simplified builder. The full WorkflowBuilder
    will be migrated in a later phase.
    """
    return {
        "_meta": {"version": "gateway_v1"},
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "mode": mode.value if hasattr(mode, "value") else mode,
        "model": model.value if hasattr(model, "value") else model,
        "width": width,
        "height": height,
        "num_frames": num_frames,
        "fps": fps,
        "steps": steps,
        "cfg": cfg,
        "shift": shift,
        "seed": seed,
    }


@router.post("/generate")
async def generate_video(
    prompt: str = Form(...),
    negative_prompt: str = Form(""),
    model: ModelType = Form(ModelType.A14B),
    mode: GenerateMode = Form(GenerateMode.T2V),
    width: int = Form(832),
    height: int = Form(480),
    num_frames: int = Form(81),
    fps: int = Form(16),
    steps: int = Form(20),
    cfg: float = Form(6.0),
    shift: float = Form(8.0),
    seed: int = Form(-1),
    # Pre-built workflow JSON (advanced usage, skips workflow builder)
    workflow_json: Optional[str] = Form(None),
    # Image file (required for I2V mode unless workflow_json is provided)
    image: Optional[UploadFile] = File(None),
    # Options
    extract_last_frame: bool = Form(False),
    gateway: TaskGateway = Depends(get_gateway),
    cos_client: COSClient = Depends(get_cos_client),
):
    """Submit a video generation task.

    For T2V mode: only prompt is required.
    For I2V mode: prompt + image required (or workflow_json).

    If workflow_json is provided, it is used directly (advanced).
    Otherwise, a simple workflow structure is built from parameters.
    """
    # 1. Validate mode + image
    if mode == GenerateMode.I2V and not image and not workflow_json:
        raise HTTPException(
            status_code=400,
            detail="I2V mode requires an image or pre-built workflow_json",
        )

    # 2. Validate workflow_json if provided
    if workflow_json is not None:
        try:
            json.loads(workflow_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid workflow_json: {exc}",
            )

    # 3. Handle image upload to COS
    input_files: list[dict] = []
    if image:
        image_data = await image.read()
        original_filename = image.filename or "upload.png"
        unique_filename = f"{uuid.uuid4().hex}_{original_filename}"

        # Write to a temp file so COS client can upload
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{original_filename}") as tmp:
            tmp.write(image_data)
            tmp_path = tmp.name

        try:
            cos_url = cos_client.upload_file(tmp_path, "inputs", unique_filename)
        except Exception as exc:
            logger.warning("COS upload failed (disabled?): %s", exc)
            cos_url = ""

        input_files.append({
            "cos_url": cos_url,
            "original_filename": original_filename,
            "placeholder": "__INPUT_IMAGE__",
        })

    # 4. Build or parse workflow
    if workflow_json:
        workflow = json.loads(workflow_json)
    else:
        workflow = _build_simple_workflow(
            prompt=prompt,
            negative_prompt=negative_prompt,
            mode=mode,
            model=model,
            width=width,
            height=height,
            num_frames=num_frames,
            fps=fps,
            steps=steps,
            cfg=cfg,
            shift=shift,
            seed=seed,
        )

    # 5. Create task via TaskGateway
    params = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "width": width,
        "height": height,
        "num_frames": num_frames,
        "fps": fps,
        "steps": steps,
        "cfg": cfg,
        "shift": shift,
        "seed": seed,
        "extract_last_frame": extract_last_frame,
    }

    task_id = await gateway.create_task(
        mode=mode,
        model=model,
        workflow=workflow,
        params=params,
    )

    # 6. Store input_files on the task (if any)
    if input_files:
        await gateway.redis.hset(
            task_key(task_id),
            mapping={"input_files": json.dumps(input_files)},
        )

    return {"task_id": task_id, "status": "queued"}
