"""Post-processing API routes: frame interpolation, upscale, AI audio.

Each endpoint accepts either a task_id (to load its output video) or a direct
video file upload. The video is then processed via ComfyUI and a new task is returned.
"""
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import Optional
from api.models.enums import GenerateMode, ModelType, TaskStatus
from api.middleware.auth import verify_api_key
from api.services import storage

logger = logging.getLogger(__name__)
router = APIRouter()


class PostprocessResponse:
    """Simple response — we use dict return instead of Pydantic for Form endpoints."""
    pass


async def _resolve_video_path(
    task_id: Optional[str] = None,
    video: Optional[UploadFile] = None,
) -> Path:
    """Resolve video path from either task_id or uploaded file."""
    if video and video.filename:
        # Upload video to ComfyUI input directory
        from api.config import COMFYUI_PATH
        input_dir = COMFYUI_PATH / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        video_data = await video.read()
        if not video_data:
            raise HTTPException(400, "Uploaded video is empty")
        # Save with unique name
        import uuid
        ext = Path(video.filename).suffix or ".mp4"
        filename = f"pp_{uuid.uuid4().hex[:12]}{ext}"
        video_path = input_dir / filename
        video_path.write_bytes(video_data)
        logger.info("Saved uploaded video to %s (%d bytes)", video_path, len(video_data))
        return video_path

    if task_id:
        from api.main import task_manager
        task = await task_manager.get_task(task_id)
        if not task:
            raise HTTPException(404, "Source task not found")
        if task["status"] != TaskStatus.COMPLETED.value:
            raise HTTPException(400, "Source task is not completed")
        video_url = task.get("video_url")
        if not video_url:
            raise HTTPException(400, "Source task has no video output")
        video_path = await storage.get_video_path_from_url(video_url)
        if not video_path or not video_path.exists():
            raise HTTPException(404, "Video file not found on disk")
        return video_path

    raise HTTPException(400, "Either task_id or video file is required")


@router.post("/postprocess/interpolate")
async def interpolate_video(
    video: UploadFile = File(None),
    task_id: Optional[str] = Form(None),
    multiplier: int = Form(2),
    resolution_profile: str = Form("small"),
    fps: float = Form(16.0),
    _=Depends(verify_api_key),
):
    """Frame interpolation using RIFE TensorRT."""
    from api.main import task_manager
    from api.services.workflow_builder import build_interpolate_workflow

    video_path = await _resolve_video_path(task_id, video)

    workflow = build_interpolate_workflow(
        video_path=str(video_path),
        multiplier=multiplier,
        resolution_profile=resolution_profile,
        fps=fps,
    )

    new_task_id = await task_manager.create_task(
        mode=GenerateMode.I2V,
        model=ModelType.A14B,
        workflow=workflow,
        params={"postprocess": "interpolate", "source": task_id or video_path.name,
                "multiplier": multiplier},
    )
    return {"task_id": new_task_id, "message": "Post-processing task created"}


@router.post("/postprocess/upscale")
async def upscale_video(
    video: UploadFile = File(None),
    task_id: Optional[str] = Form(None),
    model: str = Form("4x-UltraSharp"),
    resize_to: str = Form("FHD"),
    fps: float = Form(16.0),
    _=Depends(verify_api_key),
):
    """Upscale video using TensorRT upscaler."""
    from api.main import task_manager
    from api.services.workflow_builder import build_upscale_workflow

    video_path = await _resolve_video_path(task_id, video)

    workflow = build_upscale_workflow(
        video_path=str(video_path),
        model=model,
        resize_to=resize_to,
        fps=fps,
    )

    new_task_id = await task_manager.create_task(
        mode=GenerateMode.I2V,
        model=ModelType.A14B,
        workflow=workflow,
        params={"postprocess": "upscale", "source": task_id or video_path.name,
                "model": model, "resize_to": resize_to},
    )
    return {"task_id": new_task_id, "message": "Post-processing task created"}


@router.post("/postprocess/audio")
async def add_audio(
    video: UploadFile = File(None),
    task_id: Optional[str] = Form(None),
    prompt: str = Form(""),
    negative_prompt: str = Form(""),
    steps: int = Form(25),
    cfg: float = Form(4.5),
    fps: float = Form(16.0),
    _=Depends(verify_api_key),
):
    """Add AI-generated audio to video using MMAudio."""
    from api.main import task_manager
    from api.services.workflow_builder import build_audio_workflow

    video_path = await _resolve_video_path(task_id, video)

    workflow = build_audio_workflow(
        video_path=str(video_path),
        fps=fps,
        prompt=prompt,
        negative_prompt=negative_prompt,
        steps=steps,
        cfg=cfg,
    )

    new_task_id = await task_manager.create_task(
        mode=GenerateMode.I2V,
        model=ModelType.A14B,
        workflow=workflow,
        params={"postprocess": "audio", "source": task_id or video_path.name,
                "prompt": prompt},
    )
    return {"task_id": new_task_id, "message": "Post-processing task created"}
