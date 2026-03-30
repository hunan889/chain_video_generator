"""Unified image/video transform API for H5 frontend."""

import base64
import io
import json
import logging
import math
from typing import Optional

from PIL import Image

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from api.middleware.auth import verify_api_key
from api.routes.image import (
    FORGE_URL,
    SCENE_SWAP_DEFAULT_PROMPT,
    SEEDREAM_MODEL,
    ImageResponse,
    _async_post,
    _blend_expression,
    _call_byteplus_async,
    _crop_and_resize,
    _overlay_face_only,
    _parse_size,
    _save_result_image,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Scene template definitions
# ---------------------------------------------------------------------------

VALID_SCENES = {"face_swap", "pose", "clothes", "shoot", "puzzle", "photo_edit", "eraser"}

SCENE_PROMPTS = {
    "clothes": (
        "keep the person from image 1 unchanged, "
        "change their outfit to match the clothing shown in image 2, "
        "maintain natural body proportions and pose"
    ),
    "shoot": (
        "place the person from image 1 into the scene and style of image 2, "
        "professional photography, natural lighting"
    ),
    "puzzle": (
        "creatively combine the person from image 1 "
        "with the elements of image 2, seamless composition"
    ),
    "eraser": (
        "remove all clothing from the person in the image, "
        "generate realistic natural body underneath, "
        "maintain the same pose and background"
    ),
}

# Scenes that require a reference image
REFERENCE_REQUIRED = {"face_swap", "pose", "clothes", "shoot", "puzzle"}

# Scenes that require a user prompt
PROMPT_REQUIRED = {"photo_edit"}


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

class TransformResponse(BaseModel):
    url: str
    scene: str
    size: str
    seed: Optional[int] = None


# ---------------------------------------------------------------------------
# POST /api/v1/image/transform
# ---------------------------------------------------------------------------

@router.post("/image/transform", response_model=TransformResponse)
async def image_transform(
    scene: str = Form(...),
    image: UploadFile = File(...),
    reference: Optional[UploadFile] = File(None),
    prompt: Optional[str] = Form(None),
    size: str = Form("adaptive"),
    seed: Optional[int] = Form(None),
    advanced: bool = Form(False),
    options: str = Form("{}"),
    _user=Depends(verify_api_key),
):
    """Unified image transform endpoint with scene-based routing."""

    # --- Validate scene ---
    if scene not in VALID_SCENES:
        raise HTTPException(422, f"Unknown scene: {scene!r}. Valid: {sorted(VALID_SCENES)}")

    # --- Validate required params ---
    if scene in REFERENCE_REQUIRED and reference is None:
        raise HTTPException(400, f"Scene '{scene}' requires a reference image")

    if scene in PROMPT_REQUIRED and not (prompt and prompt.strip()):
        raise HTTPException(400, f"Scene '{scene}' requires a prompt")

    # --- Parse options ---
    try:
        opts = json.loads(options) if options else {}
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON in 'options' parameter")

    # --- Read image data ---
    image_data = await image.read()
    if len(image_data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image too large (max 10MB)")

    reference_data = None
    if reference is not None:
        reference_data = await reference.read()
        if len(reference_data) > 10 * 1024 * 1024:
            raise HTTPException(400, "Reference image too large (max 10MB)")

    # --- Resolve adaptive size from uploaded image ---
    if size.lower() == "adaptive":
        try:
            img = Image.open(io.BytesIO(image_data))
            w, h = img.size
            # Align to 64px for model compatibility
            w = math.ceil(w / 64) * 64
            h = math.ceil(h / 64) * 64
            size = f"{w}x{h}"
            logger.info("[transform] adaptive size resolved: %s", size)
        except Exception:
            size = "1024x1024"
            logger.warning("[transform] failed to read image dimensions, falling back to %s", size)

    # --- Route to engine ---
    if scene in ("face_swap", "pose"):
        return await _engine_reactor(
            scene, image_data, reference_data, prompt, size, seed, advanced, opts
        )
    elif scene in ("clothes", "shoot", "puzzle"):
        return await _engine_seedream_ref(
            scene, image_data, reference_data, prompt, size, seed, opts
        )
    elif scene in ("photo_edit", "eraser"):
        return await _engine_seedream_edit(
            scene, image_data, prompt, size, seed, opts
        )

    raise HTTPException(422, f"Scene '{scene}' not implemented")


# ---------------------------------------------------------------------------
# POST /api/v1/video/transform
# ---------------------------------------------------------------------------

@router.post("/video/transform")
async def video_transform(
    scene: str = Form(...),
    video: UploadFile = File(...),
    reference: UploadFile = File(...),
    faces_index: str = Form("0"),
    _user=Depends(verify_api_key),
):
    """Unified video transform endpoint (async, returns task_id)."""

    if scene != "face_swap":
        raise HTTPException(422, f"Video scene '{scene}' not supported. Currently supported: face_swap")

    from api.main import task_manager
    from api.services.workflow_builder import load_workflow

    # Read files
    video_data = await video.read()
    if len(video_data) > 500 * 1024 * 1024:
        raise HTTPException(400, "Video too large (max 500MB)")

    face_data = await reference.read()
    if len(face_data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Reference image too large (max 10MB)")

    # Save uploads
    from api.services import storage
    video_filename, _ = await storage.save_upload(video_data, video.filename or "input.mp4")
    face_filename, _ = await storage.save_upload(face_data, reference.filename or "face.png")

    # Upload to ComfyUI
    client = task_manager._get_client("a14b")
    if not client or not await client.is_alive():
        raise HTTPException(503, "Video processing service unavailable")

    video_upload = await client.upload_video(video_data, video_filename)
    face_upload = await client.upload_image(face_data, face_filename)

    # Load and configure workflow
    workflow = load_workflow("video_faceswap")
    workflow["1"]["inputs"]["video"] = video_upload.get("name", video_filename)
    workflow["4"]["inputs"]["image"] = face_upload.get("name", face_filename)
    workflow["5"]["inputs"]["faces_index"] = faces_index

    # Submit
    prompt_id = await client.queue_prompt(workflow)
    task_id = await task_manager.create_task(
        "video_faceswap",
        {"video": video_filename, "face": face_filename, "faces_index": faces_index},
        prompt_id,
        "a14b",
    )

    return {"task_id": task_id, "status": "queued", "scene": "face_swap"}


# ---------------------------------------------------------------------------
# Engine: Reactor (face_swap, pose)
# ---------------------------------------------------------------------------

async def _engine_reactor(
    scene: str,
    image_data: bytes,
    reference_data: bytes,
    prompt: Optional[str],
    size: str,
    seed: Optional[int],
    advanced: bool,
    opts: dict,
) -> TransformResponse:
    """Reactor face swap, optionally refined by SeedDream MultiRef."""

    expression_keep = float(opts.get("expression_keep", 0.0))
    preserve_occlusion = bool(opts.get("preserve_occlusion", False))
    codeformer_weight = float(opts.get("codeformer_weight", 0.7))
    restorer_visibility = float(opts.get("restorer_visibility", 1.0))
    det_thresh = float(opts.get("det_thresh", 0.5))
    occlusion_percentile = float(opts.get("occlusion_percentile", 60))

    # Parse output size
    parsed_size = _parse_size(size)
    output_w, output_h = map(int, parsed_size.lower().split("x"))
    crop_w = min(output_w, 1024)
    crop_h = min(output_h, 1024)
    if output_w != crop_w or output_h != crop_h:
        ratio = output_w / output_h
        if crop_w / crop_h > ratio:
            crop_w = int(crop_h * ratio)
        else:
            crop_h = int(crop_w / ratio)
    target_w, target_h = crop_w, crop_h

    # Crop both images
    face_cropped = _crop_and_resize(image_data, target_w, target_h)
    scene_cropped = _crop_and_resize(reference_data, target_w, target_h)
    face_b64 = base64.b64encode(face_cropped).decode()
    scene_b64 = base64.b64encode(scene_cropped).decode()

    # Step 1: Reactor face swap
    reactor_payload = {
        "source_image": face_b64,
        "target_image": scene_b64,
        "source_faces_index": [0],
        "face_index": [0],
        "model": "inswapper_128.onnx",
        "face_restorer": "CodeFormer",
        "restorer_visibility": restorer_visibility,
        "codeformer_weight": codeformer_weight,
        "restore_first": 1,
        "upscaler": "None",
        "scale": 1,
        "upscale_visibility": 1,
        "device": "CUDA",
        "mask_face": 1,
        "det_thresh": det_thresh,
        "det_maxnum": 0,
    }

    logger.info("[transform/%s] Reactor face swap (%dx%d)...", scene, target_w, target_h)
    try:
        reactor_resp = await _async_post(
            f"{FORGE_URL}/reactor/image", json=reactor_payload, timeout=120
        )
    except Exception as e:
        raise HTTPException(502, f"Face swap request failed: {e}")

    if reactor_resp.status_code != 200:
        raise HTTPException(502, f"Face swap failed: {reactor_resp.text[:300]}")

    try:
        swapped_b64 = reactor_resp.json()["image"]
    except (KeyError, ValueError) as e:
        raise HTTPException(502, f"Unexpected face swap response: {e}")

    swapped_data = base64.b64decode(swapped_b64)

    # Step 1.5: Post-process
    if preserve_occlusion:
        swapped_data = _overlay_face_only(scene_cropped, swapped_data, occlusion_percentile)
    if expression_keep > 0:
        swapped_data = _blend_expression(scene_cropped, swapped_data, expression_keep)

    swapped_b64_out = base64.b64encode(swapped_data).decode()

    # Basic mode (or pose scene): return Reactor result directly
    if not advanced or scene == "pose":
        url = _save_result_image(swapped_b64_out)
        logger.info("[transform/%s] completed (basic): %s", scene, url)
        return TransformResponse(url=url, scene=scene, size=f"{target_w}x{target_h}", seed=seed)

    # Advanced mode: refine with SeedDream MultiRef
    image_list = [
        f"data:image/jpeg;base64,{face_b64}",
        f"data:image/jpeg;base64,{swapped_b64_out}",
    ]
    full_prompt = prompt or SCENE_SWAP_DEFAULT_PROMPT
    if preserve_occlusion:
        full_prompt += (
            ", IMPORTANT: do not remove any objects from the face or mouth area, "
            "keep all occlusions exactly as they appear in image 2, "
            "preserve everything covering the face"
        )

    payload = {
        "model": SEEDREAM_MODEL,
        "prompt": full_prompt,
        "image": image_list,
        "size": parsed_size,
        "response_format": "url",
        "watermark": False,
    }
    if seed is not None:
        payload["seed"] = seed

    logger.info("[transform/%s] advanced refinement...", scene)
    try:
        url = await _call_byteplus_async(payload)
        logger.info("[transform/%s] completed (advanced): %s", scene, url[:120])
        return TransformResponse(url=url, scene=scene, size=parsed_size, seed=seed)
    except HTTPException:
        # Fallback to Reactor result
        url = _save_result_image(swapped_b64_out)
        logger.warning("[transform/%s] advanced refinement failed, returning basic result", scene)
        return TransformResponse(url=url, scene=scene, size=f"{target_w}x{target_h}", seed=seed)


# ---------------------------------------------------------------------------
# Engine: SeedDream MultiRef (clothes, shoot, puzzle)
# ---------------------------------------------------------------------------

async def _engine_seedream_ref(
    scene: str,
    image_data: bytes,
    reference_data: bytes,
    prompt: Optional[str],
    size: str,
    seed: Optional[int],
    opts: dict,
) -> TransformResponse:
    """SeedDream MultiRef: merge two images with prompt guidance."""

    parsed_size = _parse_size(size)
    target_w, target_h = map(int, parsed_size.lower().split("x"))

    # Crop both images to match output aspect ratio
    image_cropped = _crop_and_resize(image_data, target_w, target_h)
    ref_cropped = _crop_and_resize(reference_data, target_w, target_h)

    image_b64 = base64.b64encode(image_cropped).decode()
    ref_b64 = base64.b64encode(ref_cropped).decode()

    image_list = [
        f"data:image/jpeg;base64,{image_b64}",
        f"data:image/jpeg;base64,{ref_b64}",
    ]

    # Build prompt: scene default + optional user supplement
    default_prompt = SCENE_PROMPTS.get(scene, "")
    if prompt and prompt.strip():
        full_prompt = f"{default_prompt}, {prompt.strip()}" if default_prompt else prompt.strip()
    else:
        full_prompt = default_prompt

    payload = {
        "model": opts.get("model") or SEEDREAM_MODEL,
        "prompt": full_prompt,
        "image": image_list,
        "size": parsed_size,
        "response_format": "url",
        "watermark": False,
    }
    if seed is not None:
        payload["seed"] = seed

    logger.info("[transform/%s] SeedDream multiref, prompt=%.100s", scene, full_prompt)
    url = await _call_byteplus_async(payload)
    logger.info("[transform/%s] completed: %s", scene, url[:120])
    return TransformResponse(url=url, scene=scene, size=parsed_size, seed=seed)


# ---------------------------------------------------------------------------
# Engine: SeedDream I2I Edit (photo_edit, eraser)
# ---------------------------------------------------------------------------

async def _engine_seedream_edit(
    scene: str,
    image_data: bytes,
    prompt: Optional[str],
    size: str,
    seed: Optional[int],
    opts: dict,
) -> TransformResponse:
    """SeedDream I2I: edit a single image with prompt instructions."""

    parsed_size = _parse_size(size)

    ext = "jpeg"
    mime = "image/jpeg"
    b64 = base64.b64encode(image_data).decode()
    image_url = f"data:{mime};base64,{b64}"

    # Build prompt
    if prompt and prompt.strip():
        full_prompt = prompt.strip()
    else:
        full_prompt = SCENE_PROMPTS.get(scene, "")

    if not full_prompt:
        raise HTTPException(400, f"Scene '{scene}' requires a prompt")

    payload = {
        "model": opts.get("model") or SEEDREAM_MODEL,
        "prompt": full_prompt,
        "image": image_url,
        "size": parsed_size,
        "response_format": "url",
        "watermark": False,
    }
    if seed is not None:
        payload["seed"] = seed

    logger.info("[transform/%s] SeedDream I2I, prompt=%.100s", scene, full_prompt)
    url = await _call_byteplus_async(payload)
    logger.info("[transform/%s] completed: %s", scene, url[:120])
    return TransformResponse(url=url, scene=scene, size=parsed_size, seed=seed)
