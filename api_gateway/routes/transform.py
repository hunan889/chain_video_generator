"""h5 frontend compatibility shim.

h5 frontend (Video-Gen-H5) calls these endpoints today and expects them to
work. This shim routes them to the appropriate ClothOff backend client.

Scenes mapped to ClothOff capabilities:
  image/transform:
    - eraser      -> ClothOff undress
    - clothes     -> ClothOff undress with `cloth` option
    - face_swap   -> ClothOff faceSwap (type_gen=swapface_photo)
  video/transform:
    - animate     -> ClothOff videoGenerations/animate
    - face_swap   -> ClothOff faceSwap (type_gen=swapface_video)

Scenes with no ClothOff equivalent return 501 Not Implemented:
    pose, shoot, puzzle, photo_edit
"""

from __future__ import annotations

import logging
import mimetypes
import os
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from api_gateway.routes import clothoff as co

router = APIRouter(tags=["h5-compat"])
logger = logging.getLogger(__name__)

# Upload storage (shared with clothoff results dir for unified serving)
UPLOAD_DIR = os.path.join(co.RESULTS_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Max sizes
MAX_IMAGE = 60 * 1024 * 1024
MAX_VIDEO = 300 * 1024 * 1024

UNSUPPORTED_IMAGE_SCENES = {"pose", "shoot", "puzzle", "photo_edit"}
SUPPORTED_IMAGE_SCENES = {"eraser", "clothes", "face_swap"}
SUPPORTED_VIDEO_SCENES = {"animate", "face_swap"}

# Per-scene AI engine routing. The h5-auth proxy reads its admin config
# and appends an `engine` field to the multipart body before forwarding
# here. Only "clothoff" has a wired backend in this service today —
# "seedream" and "local" fall through to a 501 explaining the state.
ALLOWED_ENGINES = {"clothoff", "seedream", "local"}


def _reject_non_clothoff(engine: str, scene: str) -> None:
    """Raise 501 for engines that have no wired backend here yet.

    Centralized so both image/ and video/ transform return the same
    error envelope for admins/frontends to recognize ("engine not
    deployed yet"). Unknown engines get the same treatment since we'd
    rather fail loudly than silently fall back.
    """
    if engine == "clothoff":
        return
    if engine in ALLOWED_ENGINES:
        raise HTTPException(
            status_code=501,
            detail=(
                f"Engine '{engine}' is configured for scene '{scene}' but its "
                f"backend is not yet deployed on this service. Flip the admin "
                f"engine switch to 'clothoff' or wait for the backend to land."
            ),
        )
    raise HTTPException(
        status_code=422,
        detail=f"Unknown engine {engine!r} for scene {scene!r}",
    )


def _ext_for(filename: Optional[str], content_type: Optional[str], default: str = "bin") -> str:
    if filename and "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    if content_type:
        guessed = mimetypes.guess_extension(content_type)
        if guessed:
            return guessed.lstrip(".")
    return default


# ---------------------------------------------------------------------------
# POST /api/v1/upload  (h5 proxies /api/ai/upload here)
# ---------------------------------------------------------------------------


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> JSONResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(data) > MAX_VIDEO:
        raise HTTPException(status_code=400, detail="File too large (max 300MB)")
    ext = _ext_for(file.filename, file.content_type, "bin")
    filename = f"{uuid.uuid4().hex}.{ext}"
    fpath = os.path.join(UPLOAD_DIR, filename)
    with open(fpath, "wb") as fh:
        fh.write(data)
    url = f"/api/v1/clothoff/results/uploads/{filename}"
    logger.info("[transform/upload] saved %s (%d bytes)", url, len(data))
    return JSONResponse(
        {
            "success": True,
            "data": {
                "url": url,
                "filename": filename,
                "size": len(data),
            },
            "url": url,
            "filename": filename,
            "size": len(data),
        }
    )


# ---------------------------------------------------------------------------
# POST /api/v1/image/transform
# ---------------------------------------------------------------------------


@router.post("/image/transform")
async def image_transform(
    scene: str = Form(...),
    image: UploadFile = File(...),
    reference: Optional[UploadFile] = File(None),
    engine: str = Form("clothoff"),
    prompt: Optional[str] = Form(None),  # noqa: ARG001 — accepted for h5 compat
    size: str = Form("adaptive"),  # noqa: ARG001
    seed: Optional[int] = Form(None),  # noqa: ARG001
    advanced: bool = Form(False),  # noqa: ARG001
    options: str = Form("{}"),  # noqa: ARG001
    cloth: Optional[str] = Form(None),
    bodyType: Optional[str] = Form(None),
    agePeople: Optional[str] = Form(None),
    breastSize: Optional[str] = Form(None),
    buttSize: Optional[str] = Form(None),
    pose: Optional[str] = Form(None),
    poseId: Optional[str] = Form(None),
) -> JSONResponse:
    scene = (scene or "").strip()
    engine = (engine or "clothoff").strip()
    if scene in UNSUPPORTED_IMAGE_SCENES:
        raise HTTPException(
            status_code=501,
            detail=(
                f"Scene '{scene}' not supported by ClothOff backend. "
                f"Disable in frontend or pick a supported scene."
            ),
        )
    if scene not in SUPPORTED_IMAGE_SCENES:
        raise HTTPException(status_code=422, detail=f"Unknown scene: {scene!r}")
    # Engine dispatch — only clothoff is wired here; anything else gets a
    # clear 501 so the admin can see the misconfiguration.
    _reject_non_clothoff(engine, scene)

    image_data = await image.read()
    if not image_data:
        raise HTTPException(status_code=400, detail="image empty")
    if len(image_data) > MAX_IMAGE:
        raise HTTPException(status_code=400, detail="image too large (max 60MB)")

    image_name = image.filename or "image.jpg"
    image_mime = image.content_type or mimetypes.guess_type(image_name)[0] or "image/jpeg"

    if scene == "eraser":
        logger.info("[transform/image] scene=eraser")
        result = await co.submit_eraser(
            image_data,
            image_name=image_name,
            image_mime=image_mime,
            cloth=cloth,
            body_type=bodyType,
            age_people=agePeople,
            breast_size=breastSize,
            butt_size=buttSize,
            pose=pose,
            pose_id=poseId,
        )
        return JSONResponse(
            {
                "url": result["url"],
                "scene": "eraser",
                "size": "adaptive",
                "seed": None,
            }
        )

    if scene == "clothes":
        logger.info("[transform/image] scene=clothes cloth=%s", cloth or "Bikini")
        result = await co.submit_eraser(
            image_data,
            image_name=image_name,
            image_mime=image_mime,
            cloth=cloth or "Bikini",
            body_type=bodyType,
            age_people=agePeople,
            breast_size=breastSize,
            butt_size=buttSize,
            pose=pose,
            pose_id=poseId,
        )
        return JSONResponse(
            {
                "url": result["url"],
                "scene": "clothes",
                "size": "adaptive",
                "seed": None,
            }
        )

    # scene == face_swap
    if reference is None:
        raise HTTPException(
            status_code=400,
            detail="scene 'face_swap' requires a reference image",
        )
    ref_data = await reference.read()
    if not ref_data:
        raise HTTPException(status_code=400, detail="reference empty")
    if len(ref_data) > MAX_IMAGE:
        raise HTTPException(status_code=400, detail="reference too large (max 60MB)")

    # h5 convention: image = face to insert, reference = scene/target
    # ClothOff convention: target_image = face photo, input_pv = scene to be edited
    logger.info("[transform/image] scene=face_swap (photo)")
    result = await co.submit_face_swap(
        input_pv_data=ref_data,
        input_pv_name=reference.filename or "scene.jpg",
        input_pv_mime=reference.content_type or mimetypes.guess_type(reference.filename or "")[0] or "image/jpeg",
        target_image_data=image_data,
        target_image_name=image_name,
        target_image_mime=image_mime,
        type_gen="swapface_photo",
    )
    return JSONResponse(
        {
            "url": result["url"],
            "scene": "face_swap",
            "size": "adaptive",
            "seed": None,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/v1/video/transform
# ---------------------------------------------------------------------------


@router.post("/video/transform")
async def video_transform(
    scene: str = Form(...),
    image: Optional[UploadFile] = File(None),
    video: Optional[UploadFile] = File(None),
    reference: Optional[UploadFile] = File(None),
    engine: str = Form("clothoff"),
    model_id: Optional[str] = Form(None),
    faces_index: str = Form("0"),  # noqa: ARG001
) -> JSONResponse:
    scene = (scene or "").strip()
    engine = (engine or "clothoff").strip()
    if scene not in SUPPORTED_VIDEO_SCENES:
        raise HTTPException(
            status_code=501,
            detail=f"Video scene '{scene}' not supported. Valid: {sorted(SUPPORTED_VIDEO_SCENES)}",
        )
    _reject_non_clothoff(engine, scene)

    if scene == "animate":
        if image is None:
            raise HTTPException(status_code=400, detail="scene 'animate' requires image")
        if not model_id:
            raise HTTPException(status_code=400, detail="scene 'animate' requires model_id")
        image_data = await image.read()
        if not image_data:
            raise HTTPException(status_code=400, detail="image empty")
        if len(image_data) > MAX_IMAGE:
            raise HTTPException(status_code=400, detail="image too large (max 60MB)")
        logger.info("[transform/video] scene=animate model_id=%s", model_id)
        result = await co.submit_animate(
            image_data,
            model_id,
            image_name=image.filename or "image.jpg",
            image_mime=image.content_type or mimetypes.guess_type(image.filename or "")[0] or "image/jpeg",
        )
        # PhotoAlive.vue expects resp.url
        return JSONResponse(
            {
                "url": result["url"],
                "scene": "animate",
                "model_id": model_id,
            }
        )

    # scene == face_swap
    if video is None or reference is None:
        raise HTTPException(
            status_code=400,
            detail="scene 'face_swap' requires video and reference",
        )
    video_data = await video.read()
    ref_data = await reference.read()
    if not video_data:
        raise HTTPException(status_code=400, detail="video empty")
    if not ref_data:
        raise HTTPException(status_code=400, detail="reference empty")
    if len(video_data) > MAX_VIDEO:
        raise HTTPException(status_code=400, detail="video too large (max 300MB)")
    if len(ref_data) > MAX_IMAGE:
        raise HTTPException(status_code=400, detail="reference too large (max 60MB)")

    logger.info("[transform/video] scene=face_swap (video)")
    # h5 video face_swap: video=targetFile (scene), reference=sourceFile (face to insert)
    # ClothOff: input_pv=scene video, target_image=face to insert
    result = await co.submit_face_swap(
        input_pv_data=video_data,
        input_pv_name=video.filename or "input.mp4",
        input_pv_mime=video.content_type or mimetypes.guess_type(video.filename or "")[0] or "video/mp4",
        target_image_data=ref_data,
        target_image_name=reference.filename or "face.jpg",
        target_image_mime=reference.content_type or mimetypes.guess_type(reference.filename or "")[0] or "image/jpeg",
        type_gen="swapface_video",
    )

    # h5 AiFace.vue video mode expects {task_id} then polls /api/ai/tasks/{task_id}
    # Register the completed result so the tasks endpoint can return it.
    task_id = result["id_gen"]
    co.register_completed_task(
        task_id,
        {
            "video_url": result["url"],
            "url": result["url"],
            "scene": "face_swap",
        },
    )
    return JSONResponse(
        {
            "task_id": task_id,
            "status": "completed",
            "scene": "face_swap",
            "video_url": result["url"],
            "url": result["url"],
            "progress": 1.0,
        }
    )
