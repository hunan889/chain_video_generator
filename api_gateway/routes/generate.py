"""POST /api/v1/generate — submit a video generation task.

Supports three Content-Type modes:
  1. application/json — JSON body (T2V without file uploads)
  2. multipart/form-data — ``params`` JSON field + optional file uploads
  3. Individual Form() fields — backward compat for curl tests

Also provides POST /api/v1/generate/i2v for image-to-video (always multipart).
"""

import json
import logging
import tempfile
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from api_gateway.dependencies import get_cos_client, get_gateway
from shared.cos.client import COSClient
from shared.enums import GenerateMode, ModelType
from shared.redis_keys import task_key
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["generate"])


# ------------------------------------------------------------------
# Internal dataclass for parsed generation parameters
# ------------------------------------------------------------------

@dataclass(frozen=True)
class GenerateParams:
    """Validated, normalized parameters for a generation request."""

    prompt: str
    negative_prompt: str
    model: ModelType
    mode: GenerateMode
    width: int
    height: int
    num_frames: int
    fps: int
    steps: int
    cfg: float
    shift: float
    seed: int
    # Extra fields forwarded to the task but not used by WorkflowBuilder
    scheduler: str
    model_preset: str
    t5_preset: str
    loras: list
    upscale: bool
    auto_lora: bool
    auto_prompt: bool
    face_swap: Optional[dict]
    extract_last_frame: bool
    # I2V-specific
    noise_aug_strength: Optional[float]
    motion_amplitude: Optional[float]
    color_match: Optional[bool]
    color_match_method: Optional[str]
    resize_mode: Optional[str]
    # Pre-built workflow (advanced)
    workflow_json: Optional[str]


def _parse_params(raw: dict, *, default_mode: GenerateMode = GenerateMode.T2V) -> GenerateParams:
    """Build a GenerateParams from a raw dict (from JSON body or parsed ``params`` field).

    Applies defaults for any missing field so callers only need to send what
    they care about.
    """
    prompt = raw.get("prompt")
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    try:
        model = ModelType(raw["model"]) if raw.get("model") else ModelType.A14B
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid model: {raw.get('model')}")

    try:
        mode = GenerateMode(raw["mode"]) if raw.get("mode") else default_mode
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {raw.get('mode')}")

    return GenerateParams(
        prompt=prompt,
        negative_prompt=raw.get("negative_prompt", ""),
        model=model,
        mode=mode,
        width=int(raw.get("width", 832)),
        height=int(raw.get("height", 480)),
        num_frames=int(raw.get("num_frames", 81)),
        fps=int(raw.get("fps", 16)),
        steps=int(raw.get("steps", 20)),
        cfg=float(raw.get("cfg", 6.0)),
        shift=float(raw.get("shift", 8.0)),
        seed=int(raw.get("seed", -1) if raw.get("seed") is not None else -1),
        scheduler=raw.get("scheduler", ""),
        model_preset=raw.get("model_preset", ""),
        t5_preset=raw.get("t5_preset", ""),
        loras=raw.get("loras") or [],
        upscale=bool(raw.get("upscale", False)),
        auto_lora=bool(raw.get("auto_lora", False)),
        auto_prompt=bool(raw.get("auto_prompt", False)),
        face_swap=raw.get("face_swap"),
        extract_last_frame=bool(raw.get("extract_last_frame", False)),
        noise_aug_strength=float(raw["noise_aug_strength"]) if raw.get("noise_aug_strength") is not None else None,
        motion_amplitude=float(raw["motion_amplitude"]) if raw.get("motion_amplitude") is not None else None,
        color_match=raw.get("color_match"),
        color_match_method=raw.get("color_match_method"),
        resize_mode=raw.get("resize_mode"),
        workflow_json=raw.get("workflow_json"),
    )


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------

def _build_workflow(
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
    image_filename: Optional[str] = None,
) -> dict:
    """Build a ComfyUI workflow dict using shared.workflow_builder.

    Falls back to a minimal dict if WorkflowBuilder cannot load templates
    (e.g. WORKFLOWS_DIR not configured or templates not found).
    """
    try:
        from shared.workflow_builder import build_workflow
        return build_workflow(
            mode=mode,
            model=model,
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            num_frames=num_frames,
            fps=fps,
            steps=steps,
            cfg=cfg,
            shift=shift,
            seed=seed if seed != -1 else None,
            image_filename=image_filename,
        )
    except Exception as exc:
        logger.warning(
            "WorkflowBuilder failed (%s), using minimal workflow fallback", exc
        )
        return {
            "_meta": {"version": "gateway_v1", "fallback": True},
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


async def _upload_file_to_cos(
    file: UploadFile,
    cos_client: COSClient,
    subdir: str = "inputs",
    placeholder: str = "__INPUT_IMAGE__",
) -> dict:
    """Read an UploadFile, upload to COS, return an input_files entry."""
    file_data = await file.read()
    original_filename = file.filename or "upload.png"
    unique_filename = f"{uuid.uuid4().hex}_{original_filename}"

    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{original_filename}") as tmp:
        tmp.write(file_data)
        tmp_path = tmp.name

    try:
        cos_url = cos_client.upload_file(tmp_path, subdir, unique_filename)
    except Exception as exc:
        logger.warning("COS upload failed (disabled?): %s", exc)
        cos_url = ""

    return {
        "cos_url": cos_url,
        "original_filename": original_filename,
        "placeholder": placeholder,
    }


def _params_to_task_dict(p: GenerateParams) -> dict:
    """Convert GenerateParams to the dict stored in Redis task ``params``."""
    result = {
        "prompt": p.prompt,
        "negative_prompt": p.negative_prompt,
        "width": p.width,
        "height": p.height,
        "num_frames": p.num_frames,
        "fps": p.fps,
        "steps": p.steps,
        "cfg": p.cfg,
        "shift": p.shift,
        "seed": p.seed,
        "extract_last_frame": p.extract_last_frame,
    }
    # Optional / extra fields -- only include if set
    if p.scheduler:
        result["scheduler"] = p.scheduler
    if p.model_preset:
        result["model_preset"] = p.model_preset
    if p.t5_preset:
        result["t5_preset"] = p.t5_preset
    if p.loras:
        result["loras"] = p.loras
    if p.upscale:
        result["upscale"] = p.upscale
    if p.auto_lora:
        result["auto_lora"] = p.auto_lora
    if p.auto_prompt:
        result["auto_prompt"] = p.auto_prompt
    if p.face_swap:
        result["face_swap"] = p.face_swap
    # I2V-specific
    if p.noise_aug_strength is not None:
        result["noise_aug_strength"] = p.noise_aug_strength
    if p.motion_amplitude is not None:
        result["motion_amplitude"] = p.motion_amplitude
    if p.color_match is not None:
        result["color_match"] = p.color_match
    if p.color_match_method:
        result["color_match_method"] = p.color_match_method
    if p.resize_mode:
        result["resize_mode"] = p.resize_mode
    return result


async def _create_generation_task(
    p: GenerateParams,
    gateway: TaskGateway,
    cos_client: COSClient,
    image: Optional[UploadFile] = None,
    face_image: Optional[UploadFile] = None,
) -> dict:
    """Shared logic for creating a generation task (T2V or I2V).

    Returns ``{"task_id": ..., "status": "queued"}``.
    """
    # 1. Validate mode + image
    if p.mode == GenerateMode.I2V and not image and not p.workflow_json:
        raise HTTPException(
            status_code=400,
            detail="I2V mode requires an image or pre-built workflow_json",
        )

    # 2. Validate workflow_json if provided
    if p.workflow_json is not None:
        try:
            json.loads(p.workflow_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid workflow_json: {exc}",
            )

    # 3. Handle file uploads to COS
    input_files: list[dict] = []

    if image:
        entry = await _upload_file_to_cos(
            image, cos_client, subdir="inputs", placeholder="__INPUT_IMAGE__",
        )
        input_files.append(entry)

    if face_image:
        entry = await _upload_file_to_cos(
            face_image, cos_client, subdir="inputs", placeholder="__FACE_IMAGE__",
        )
        input_files.append(entry)

    # 4. Build or parse workflow
    image_filename: Optional[str] = None
    if any(f["placeholder"] == "__INPUT_IMAGE__" for f in input_files):
        image_filename = "__INPUT_IMAGE__"

    if p.workflow_json:
        workflow = json.loads(p.workflow_json)
    else:
        workflow = _build_workflow(
            prompt=p.prompt,
            negative_prompt=p.negative_prompt,
            mode=p.mode,
            model=p.model,
            width=p.width,
            height=p.height,
            num_frames=p.num_frames,
            fps=p.fps,
            steps=p.steps,
            cfg=p.cfg,
            shift=p.shift,
            seed=p.seed,
            image_filename=image_filename,
        )

    # 5. Create task via TaskGateway
    task_params = _params_to_task_dict(p)

    task_id = await gateway.create_task(
        mode=p.mode,
        model=p.model,
        workflow=workflow,
        params=task_params,
    )

    # 6. Store input_files on the task (if any)
    if input_files:
        await gateway.redis.hset(
            task_key(task_id),
            mapping={"input_files": json.dumps(input_files)},
        )

    return {"task_id": task_id, "status": "queued"}


# ------------------------------------------------------------------
# Content-Type detection helper
# ------------------------------------------------------------------

def _is_json_request(request: Request) -> bool:
    """Return True when the request Content-Type is JSON."""
    ct = request.headers.get("content-type", "")
    return "application/json" in ct


def _is_form_request(request: Request) -> bool:
    """Return True for multipart/form-data OR application/x-www-form-urlencoded."""
    ct = request.headers.get("content-type", "")
    return "multipart/form-data" in ct or "application/x-www-form-urlencoded" in ct


# ------------------------------------------------------------------
# POST /api/v1/generate  (T2V, or T2V with face swap)
# ------------------------------------------------------------------

@router.post("/generate")
async def generate_video(
    request: Request,
    gateway: TaskGateway = Depends(get_gateway),
    cos_client: COSClient = Depends(get_cos_client),
):
    """Submit a text-to-video generation task.

    Accepts three Content-Type modes:
      - application/json: JSON body with all parameters
      - multipart/form-data with ``params`` JSON field + optional files
      - multipart/form-data with individual Form fields (backward compat)
    """
    image: Optional[UploadFile] = None
    face_image: Optional[UploadFile] = None

    if _is_json_request(request):
        # --- Mode 1: JSON body ---
        try:
            raw = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}")
        p = _parse_params(raw, default_mode=GenerateMode.T2V)

    elif _is_form_request(request):
        # --- Mode 2/3: multipart/form-data or url-encoded form ---
        form = await request.form()

        # Check if there's a ``params`` JSON field (mode 2)
        params_field = form.get("params")
        if params_field is not None:
            params_str = params_field if isinstance(params_field, str) else await params_field.read()
            if isinstance(params_str, bytes):
                params_str = params_str.decode("utf-8")
            try:
                raw = json.loads(params_str)
            except (json.JSONDecodeError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=f"Invalid params JSON: {exc}")
        else:
            # Mode 3: individual form fields (backward compat)
            raw = {}
            for key in (
                "prompt", "negative_prompt", "model", "mode",
                "width", "height", "num_frames", "fps",
                "steps", "cfg", "shift", "seed",
                "scheduler", "model_preset", "t5_preset",
                "upscale", "auto_lora", "auto_prompt",
                "extract_last_frame", "workflow_json",
            ):
                val = form.get(key)
                if val is not None:
                    raw[key] = val
            # Handle loras (might be JSON string)
            loras_val = form.get("loras")
            if loras_val:
                try:
                    raw["loras"] = json.loads(loras_val) if isinstance(loras_val, str) else loras_val
                except (json.JSONDecodeError, TypeError):
                    raw["loras"] = []

        # Extract file uploads
        image_field = form.get("image")
        if image_field is not None and hasattr(image_field, "read"):
            image = image_field

        face_image_field = form.get("face_image")
        if face_image_field is not None and hasattr(face_image_field, "read"):
            face_image = face_image_field

        p = _parse_params(raw, default_mode=GenerateMode.T2V)
    else:
        raise HTTPException(
            status_code=415,
            detail="Unsupported Content-Type. Use application/json or multipart/form-data.",
        )

    return await _create_generation_task(
        p, gateway, cos_client, image=image, face_image=face_image,
    )


# ------------------------------------------------------------------
# POST /api/v1/generate/i2v  (Image-to-Video, always multipart)
# ------------------------------------------------------------------

@router.post("/generate/i2v")
async def generate_i2v(
    request: Request,
    gateway: TaskGateway = Depends(get_gateway),
    cos_client: COSClient = Depends(get_cos_client),
):
    """Submit an image-to-video generation task.

    Always multipart/form-data with:
      - ``image`` file (required)
      - ``params`` JSON string field with generation parameters
      - ``face_image`` file (optional, for face swap)
    """
    if not _is_form_request(request):
        raise HTTPException(
            status_code=415,
            detail="I2V endpoint requires multipart/form-data with an image file.",
        )

    form = await request.form()

    # Extract image file (required)
    image_field = form.get("image")
    if image_field is None or not hasattr(image_field, "read"):
        raise HTTPException(status_code=400, detail="image file is required for I2V")
    image: UploadFile = image_field

    # Extract optional face_image
    face_image: Optional[UploadFile] = None
    face_image_field = form.get("face_image")
    if face_image_field is not None and hasattr(face_image_field, "read"):
        face_image = face_image_field

    # Parse params JSON field
    params_field = form.get("params")
    if params_field is not None:
        params_str = params_field if isinstance(params_field, str) else await params_field.read()
        if isinstance(params_str, bytes):
            params_str = params_str.decode("utf-8")
        try:
            raw = json.loads(params_str)
        except (json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid params JSON: {exc}")
    else:
        # Allow individual form fields as fallback
        raw = {}
        for key in (
            "prompt", "negative_prompt", "model",
            "width", "height", "num_frames", "fps",
            "steps", "cfg", "shift", "seed",
            "scheduler", "noise_aug_strength", "motion_amplitude",
            "color_match", "color_match_method", "resize_mode",
            "model_preset", "t5_preset",
            "upscale", "auto_lora", "auto_prompt",
            "extract_last_frame", "workflow_json",
        ):
            val = form.get(key)
            if val is not None:
                raw[key] = val

    p = _parse_params(raw, default_mode=GenerateMode.I2V)

    return await _create_generation_task(
        p, gateway, cos_client, image=image, face_image=face_image,
    )
