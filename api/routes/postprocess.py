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
    model: str = Form("4x_foolhardy_Remacri"),
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


@router.post("/postprocess/upscale-image")
async def upscale_image(
    image: UploadFile = File(...),
    model: str = Form("RealESRGAN_x2plus.pth"),
    _=Depends(verify_api_key),
):
    """Upscale a single image using RealESRGAN. Returns the upscaled image URL."""
    import uuid
    import asyncio
    from api.config import COMFYUI_A14B_URL, UPLOADS_DIR
    from api.services.comfyui_client import ComfyUIClient
    from api.services.workflow_builder import build_image_upscale_workflow

    image_data = await image.read()
    if not image_data:
        raise HTTPException(400, "Uploaded image is empty")

    ext = Path(image.filename).suffix.lower() if image.filename else ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    filename = f"upscale_in_{uuid.uuid4().hex[:12]}{ext}"

    client = ComfyUIClient(COMFYUI_A14B_URL)  # Uses first A14B instance via config
    try:
        # Upload image to ComfyUI
        upload_result = await client.upload_image(image_data, filename)
        comfyui_filename = upload_result.get("name", filename)

        # Build and queue workflow
        workflow = build_image_upscale_workflow(comfyui_filename, model=model)
        prompt_id = await client.queue_prompt(workflow)
        logger.info("Image upscale queued: prompt_id=%s, file=%s", prompt_id, comfyui_filename)

        # Wait for completion (up to 120s)
        deadline = asyncio.get_event_loop().time() + 120
        history = None
        while asyncio.get_event_loop().time() < deadline:
            history = await client.get_history(prompt_id)
            if history and (history.get("status", {}).get("completed") or history.get("outputs")):
                break
            await asyncio.sleep(2)

        if not history:
            raise HTTPException(504, "Image upscale timed out")

        # Find output image
        outputs = history.get("outputs", {})
        out_image = None
        for node_output in outputs.values():
            imgs = node_output.get("images", [])
            if imgs:
                out_image = imgs[0]
                break

        if not out_image:
            raise HTTPException(500, "No output image from upscale workflow")

        # Download upscaled image from ComfyUI
        out_data = await client.download_file(
            out_image["filename"],
            subfolder=out_image.get("subfolder", ""),
            file_type=out_image.get("type", "output"),
        )

        # Save to uploads dir
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        out_filename = f"upscaled_{uuid.uuid4().hex[:12]}.png"
        out_path = UPLOADS_DIR / out_filename
        out_path.write_bytes(out_data)
        logger.info("Upscaled image saved: %s (%d bytes)", out_path, len(out_data))

        return {
            "url": f"/api/v1/results/{out_filename}",
            "filename": out_filename,
            "model": model,
        }
    finally:
        await client.close()


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
