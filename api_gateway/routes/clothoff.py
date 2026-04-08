"""ClothOff API client routes.

Wraps https://api.grtkniv.net for the gime.fun product. Uses an in-memory
futures dict to match webhook callbacks to pending requests; this only works
inside a single uvicorn worker — see api_gateway/main.py worker count.

Exposes both HTTP endpoints (under /api/v1/clothoff) and importable async
helpers (submit_eraser, submit_animate, submit_face_swap, get_completed_task)
used by the h5 compatibility shim in transform.py.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import time
import uuid
from typing import Any, Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

router = APIRouter(prefix="/api/v1/clothoff", tags=["clothoff"])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — env only, no hardcoded fallback for the key
# ---------------------------------------------------------------------------

CLOTHOFF_API_KEY: Optional[str] = os.getenv("CLOTHOFF_API_KEY")
CLOTHOFF_BASE_URL: str = os.getenv("CLOTHOFF_BASE_URL", "https://api.grtkniv.net")
CLOTHOFF_WEBHOOK_BASE: str = os.getenv("CLOTHOFF_WEBHOOK_BASE", "https://h5.gime.fun/wan")

# Webhook secret — baked into the callback URL we give ClothOff so that
# unauthenticated third parties can't POST fake results to us even if
# they guess a valid id_gen. The secret is a stable random string set at
# deploy time; ClothOff treats the URL as opaque and re-uses it per task.
# When the env var is unset we fall back to the legacy (no-secret) URL so
# existing deployments keep working and can be upgraded in a follow-up.
CLOTHOFF_WEBHOOK_SECRET: str = os.getenv("CLOTHOFF_WEBHOOK_SECRET", "").strip()
CLOTHOFF_TIMEOUT: int = int(os.getenv("CLOTHOFF_TIMEOUT", "120"))
CLOTHOFF_VIDEO_TIMEOUT: int = int(os.getenv("CLOTHOFF_VIDEO_TIMEOUT", "300"))

# Endpoints
CO_UNDRESS = f"{CLOTHOFF_BASE_URL}/api/imageGenerations/undress"
CO_ANIMATE = f"{CLOTHOFF_BASE_URL}/api/videoGenerations/animate"
CO_FACE_SWAP = f"{CLOTHOFF_BASE_URL}/api/faceSwap/swap"
CO_ANIMATE_MODELS = f"{CLOTHOFF_BASE_URL}/api/videoGenerations/models"
CO_POSES_LIST = f"{CLOTHOFF_BASE_URL}/api/imageGenerations/poses"
CO_COLLECTIONS = f"{CLOTHOFF_BASE_URL}/api/imageGenerations/collections"
CO_BALANCE = f"{CLOTHOFF_BASE_URL}/api/profile/balance"
CO_SHORT_TEMPLATES = f"{CLOTHOFF_BASE_URL}/api/shortVideos/templates"
CO_SHORT_GENERATE = f"{CLOTHOFF_BASE_URL}/api/shortVideos/generate"
CO_SHORT_STATUS = f"{CLOTHOFF_BASE_URL}/api/shortVideos/status"  # + /{batchId}

# Webhook path that ClothOff will POST to
# Build the outbound webhook URL. When a secret is configured, we bake it
# into the URL path so the signed path is all ClothOff ever sees — the
# verification endpoint checks path vs env before touching any state.
if CLOTHOFF_WEBHOOK_SECRET:
    WEBHOOK_URL = f"{CLOTHOFF_WEBHOOK_BASE}/api/v1/clothoff/webhook/{CLOTHOFF_WEBHOOK_SECRET}"
else:
    WEBHOOK_URL = f"{CLOTHOFF_WEBHOOK_BASE}/api/v1/clothoff/webhook"

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# id_gen -> Future[(result_bytes, kind)] where kind is "image" or "video"
_clothoff_futures: dict[str, asyncio.Future] = {}

# task_id -> dict(status, url, video_url, scene, completed_at) for h5 task
# polling compatibility (video face_swap flow)
_completed_tasks: dict[str, dict[str, Any]] = {}

# Simple in-memory TTL cache for short-video templates
_templates_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}
_TEMPLATES_TTL_SEC = 300

# Persistent storage for results
RESULTS_DIR = os.getenv(
    "CLOTHOFF_RESULTS_DIR",
    "/usr/local/soft/chain_video_api/uploads/clothoff",
)
os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(os.path.join(RESULTS_DIR, "uploads"), exist_ok=True)
os.makedirs(os.path.join(RESULTS_DIR, "short_videos"), exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_key() -> str:
    if not CLOTHOFF_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="ClothOff backend not configured (CLOTHOFF_API_KEY missing)",
        )
    return CLOTHOFF_API_KEY


def _envelope_ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _envelope_err(msg: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": msg}


def _result_url(filename: str) -> str:
    """Public URL path served by /api/v1/clothoff/results/{filename}.

    Note: this is an api_gateway-internal path; h5-auth proxies it under
    /api/ai/... for the frontend. The frontend strips/prefixes as needed.
    """
    return f"/api/v1/clothoff/results/{filename}"


def _guess_mime(filename: Optional[str], default: str = "application/octet-stream") -> str:
    if not filename:
        return default
    mime, _ = mimetypes.guess_type(filename)
    return mime or default


def _content_type_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
        "mp4": "video/mp4",
        "webm": "video/webm",
        "mov": "video/quicktime",
    }.get(ext, "application/octet-stream")


async def _co_get(url: str, timeout: float = 15.0) -> dict[str, Any]:
    """GET request to ClothOff with auth header, returns parsed JSON."""
    headers = {"Authorization": _require_key()}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        logger.error("[clothoff] GET %s -> %d: %s", url, resp.status_code, resp.text[:300])
        raise HTTPException(
            status_code=502,
            detail=f"ClothOff upstream error {resp.status_code}",
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid ClothOff JSON: {exc}")


async def _co_post_multipart(
    url: str,
    files: dict[str, tuple[str, bytes, str]],
    data: dict[str, str],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST multipart to ClothOff (submit phase). Returns queue info dict."""
    headers = {"Authorization": _require_key()}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, files=files, data=data, headers=headers)
    if resp.status_code != 200:
        logger.error(
            "[clothoff] POST %s -> %d: %s", url, resp.status_code, resp.text[:500]
        )
        # Bubble up ClothOff's error message if it's JSON
        try:
            err = resp.json()
            detail = err.get("error") or err.get("message") or resp.text[:300]
        except ValueError:
            detail = resp.text[:300]
        raise HTTPException(status_code=502, detail=f"ClothOff API error: {detail}")
    try:
        return resp.json()
    except ValueError:
        # Some endpoints may return empty body on 200; treat as empty dict
        return {}


async def _await_webhook(
    id_gen: str, timeout: int
) -> tuple[bytes, str]:
    """Wait for the webhook future for *id_gen* and return (bytes, kind).

    kind ∈ {"image", "video"}.
    Raises HTTPException on timeout / upstream error.
    """
    future = _clothoff_futures.get(id_gen)
    if future is None:
        raise HTTPException(status_code=500, detail="Internal: future missing")
    try:
        result_bytes, kind = await asyncio.wait_for(future, timeout=timeout)
        return result_bytes, kind
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"ClothOff processing timed out after {timeout}s",
        )
    finally:
        _clothoff_futures.pop(id_gen, None)


def _persist_result(id_gen: str, data: bytes, kind: str) -> tuple[str, str]:
    """Save result bytes to RESULTS_DIR. Returns (filename, public_url)."""
    ext = "mp4" if kind == "video" else "png"
    filename = f"{id_gen}.{ext}"
    filepath = os.path.join(RESULTS_DIR, filename)
    with open(filepath, "wb") as fh:
        fh.write(data)
    logger.info(
        "[clothoff] persisted result %s (%d bytes) -> %s",
        kind, len(data), filepath,
    )
    return filename, _result_url(filename)


# ---------------------------------------------------------------------------
# Importable async helpers (used by transform.py shim)
# ---------------------------------------------------------------------------


async def submit_eraser(
    image_data: bytes,
    image_name: str = "image.jpg",
    image_mime: str = "image/jpeg",
    *,
    cloth: Optional[str] = None,
    body_type: Optional[str] = None,
    age_people: Optional[str] = None,
    breast_size: Optional[str] = None,
    butt_size: Optional[str] = None,
    pose: Optional[str] = None,
    pose_id: Optional[str] = None,
    no_skin_color: Optional[str] = None,
    no_hair_color: Optional[str] = None,
    collection_model_id: Optional[str] = None,
    post_generation: Optional[str] = None,
) -> dict[str, Any]:
    """Submit to ClothOff undress endpoint and wait for webhook callback.

    Returns a dict: {"url": public_url, "id_gen": id_gen, "filename": fn}.
    """
    _require_key()
    id_gen = uuid.uuid4().hex

    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    _clothoff_futures[id_gen] = future

    files = {"image": (image_name or "image.jpg", image_data, image_mime or "image/jpeg")}
    data: dict[str, str] = {"id_gen": id_gen, "webhook": WEBHOOK_URL}
    for k, v in (
        ("cloth", cloth),
        ("bodyType", body_type),
        ("agePeople", age_people),
        ("breastSize", breast_size),
        ("buttSize", butt_size),
        ("pose", pose),
        ("poseId", pose_id),
        ("noSkinColor", no_skin_color),
        ("noHairColor", no_hair_color),
        ("collectionModelId", collection_model_id),
        ("postGeneration", post_generation),
    ):
        if v:
            data[k] = v

    logger.info(
        "[clothoff/eraser] submit id_gen=%s webhook=%s opts=%s",
        id_gen, WEBHOOK_URL, {k: v for k, v in data.items() if k not in ("id_gen", "webhook")},
    )

    try:
        submit = await _co_post_multipart(CO_UNDRESS, files, data)
    except Exception:
        _clothoff_futures.pop(id_gen, None)
        raise

    logger.info(
        "[clothoff/eraser] queued id_gen=%s balance=%s queueTime=%ss",
        id_gen, submit.get("apiBalance"), submit.get("queueTime"),
    )

    result_bytes, kind = await _await_webhook(id_gen, CLOTHOFF_TIMEOUT)
    filename, url = _persist_result(id_gen, result_bytes, kind or "image")
    return {"url": url, "id_gen": id_gen, "filename": filename}


async def submit_animate(
    image_data: bytes,
    model_id: str,
    image_name: str = "image.jpg",
    image_mime: str = "image/jpeg",
) -> dict[str, Any]:
    """Submit to ClothOff animate endpoint and wait for webhook callback."""
    _require_key()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id required for animate")

    id_gen = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    _clothoff_futures[id_gen] = future

    files = {"image": (image_name or "image.jpg", image_data, image_mime or "image/jpeg")}
    data = {"id_gen": id_gen, "webhook": WEBHOOK_URL, "name": model_id}

    logger.info(
        "[clothoff/animate] submit id_gen=%s model=%s webhook=%s",
        id_gen, model_id, WEBHOOK_URL,
    )

    try:
        submit = await _co_post_multipart(CO_ANIMATE, files, data)
    except Exception:
        _clothoff_futures.pop(id_gen, None)
        raise

    logger.info(
        "[clothoff/animate] queued id_gen=%s balance=%s queueTime=%ss",
        id_gen, submit.get("apiBalance"), submit.get("queueTime"),
    )

    result_bytes, kind = await _await_webhook(id_gen, CLOTHOFF_VIDEO_TIMEOUT)
    filename, url = _persist_result(id_gen, result_bytes, kind or "video")
    return {"url": url, "id_gen": id_gen, "filename": filename}


async def submit_face_swap(
    input_pv_data: bytes,
    input_pv_name: str,
    input_pv_mime: str,
    target_image_data: bytes,
    target_image_name: str,
    target_image_mime: str,
    type_gen: str,
) -> dict[str, Any]:
    """Submit to ClothOff face swap. type_gen ∈ {swapface_photo, swapface_video}."""
    _require_key()
    if type_gen not in ("swapface_photo", "swapface_video"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid type_gen: {type_gen!r}",
        )

    id_gen = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    _clothoff_futures[id_gen] = future

    files = {
        "input_pv": (input_pv_name or "input", input_pv_data, input_pv_mime or "application/octet-stream"),
        "target_image": (target_image_name or "target.jpg", target_image_data, target_image_mime or "image/jpeg"),
    }
    data = {"id_gen": id_gen, "webhook": WEBHOOK_URL, "type_gen": type_gen}

    timeout = CLOTHOFF_VIDEO_TIMEOUT if type_gen == "swapface_video" else CLOTHOFF_TIMEOUT
    logger.info(
        "[clothoff/face-swap] submit id_gen=%s type_gen=%s timeout=%ss",
        id_gen, type_gen, timeout,
    )

    try:
        submit = await _co_post_multipart(CO_FACE_SWAP, files, data)
    except Exception:
        _clothoff_futures.pop(id_gen, None)
        raise

    logger.info(
        "[clothoff/face-swap] queued id_gen=%s balance=%s queueTime=%ss",
        id_gen, submit.get("apiBalance"), submit.get("queueTime"),
    )

    result_bytes, kind = await _await_webhook(id_gen, timeout)
    # Override kind by type_gen to guarantee correct file extension
    final_kind = "video" if type_gen == "swapface_video" else "image"
    filename, url = _persist_result(id_gen, result_bytes, final_kind)
    return {"url": url, "id_gen": id_gen, "filename": filename, "kind": final_kind}


def register_completed_task(task_id: str, info: dict[str, Any]) -> None:
    """Expose a completed synchronous-future task via GET /api/v1/tasks/{id}."""
    _completed_tasks[task_id] = {
        **info,
        "status": "completed",
        "progress": 1.0,
        "completed_at": time.time(),
    }
    # Simple bounded LRU: drop oldest entries if we exceed 256
    if len(_completed_tasks) > 256:
        oldest = sorted(_completed_tasks.items(), key=lambda kv: kv[1].get("completed_at", 0))
        for key, _ in oldest[: len(_completed_tasks) - 256]:
            _completed_tasks.pop(key, None)


def get_completed_task(task_id: str) -> Optional[dict[str, Any]]:
    """Return a completed task dict if present in our cache."""
    return _completed_tasks.get(task_id)


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


# NOTE on response shape:
# h5-auth's `_wan_get` wraps whatever we return in {success, data: <json>}.
# h5 frontend then reads `res.data` to get the raw payload. Therefore GET
# endpoints below return ClothOff's payload directly (a list or dict), NOT
# our own envelope — otherwise frontends would see double-wrapped data.


@router.get("/balance")
async def balance() -> JSONResponse:
    try:
        data = await _co_get(CO_BALANCE)
        return JSONResponse(data)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": str(exc.detail)},
        )


@router.get("/animate-models")
async def animate_models() -> JSONResponse:
    try:
        data = await _co_get(CO_ANIMATE_MODELS)
        return JSONResponse(data)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": str(exc.detail)},
        )


@router.get("/poses")
async def poses_list() -> JSONResponse:
    try:
        data = await _co_get(CO_POSES_LIST)
        return JSONResponse(data)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": str(exc.detail)},
        )


@router.get("/collections")
async def collections_list() -> JSONResponse:
    try:
        data = await _co_get(CO_COLLECTIONS)
        return JSONResponse(data)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": str(exc.detail)},
        )


@router.post("/eraser")
async def eraser_endpoint(
    image: UploadFile = File(...),
    bodyType: Optional[str] = Form(None),
    agePeople: Optional[str] = Form(None),
    breastSize: Optional[str] = Form(None),
    buttSize: Optional[str] = Form(None),
    pose: Optional[str] = Form(None),
    poseId: Optional[str] = Form(None),
    cloth: Optional[str] = Form(None),
    noSkinColor: Optional[str] = Form(None),
    noHairColor: Optional[str] = Form(None),
    collectionModelId: Optional[str] = Form(None),
    postGeneration: Optional[str] = Form(None),
) -> JSONResponse:
    image_data = await image.read()
    if not image_data:
        raise HTTPException(status_code=400, detail="Empty image")
    if len(image_data) > 60 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 60MB)")
    result = await submit_eraser(
        image_data,
        image_name=image.filename or "image.jpg",
        image_mime=image.content_type or _guess_mime(image.filename, "image/jpeg"),
        cloth=cloth,
        body_type=bodyType,
        age_people=agePeople,
        breast_size=breastSize,
        butt_size=buttSize,
        pose=pose,
        pose_id=poseId,
        no_skin_color=noSkinColor,
        no_hair_color=noHairColor,
        collection_model_id=collectionModelId,
        post_generation=postGeneration,
    )
    return JSONResponse(_envelope_ok(result))


@router.post("/animate")
async def animate_endpoint(
    image: UploadFile = File(...),
    name: str = Form(...),
) -> JSONResponse:
    image_data = await image.read()
    if not image_data:
        raise HTTPException(status_code=400, detail="Empty image")
    if len(image_data) > 60 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 60MB)")
    result = await submit_animate(
        image_data,
        name,
        image_name=image.filename or "image.jpg",
        image_mime=image.content_type or _guess_mime(image.filename, "image/jpeg"),
    )
    return JSONResponse(_envelope_ok(result))


@router.post("/face-swap")
async def face_swap_endpoint(
    input_pv: UploadFile = File(...),
    target_image: UploadFile = File(...),
    type_gen: str = Form("swapface_photo"),
) -> JSONResponse:
    input_data = await input_pv.read()
    target_data = await target_image.read()
    if not input_data or not target_data:
        raise HTTPException(status_code=400, detail="Both input_pv and target_image required")
    if len(input_data) > 300 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="input_pv too large (max 300MB)")
    if len(target_data) > 60 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="target_image too large (max 60MB)")
    result = await submit_face_swap(
        input_data,
        input_pv.filename or "input.bin",
        input_pv.content_type or _guess_mime(input_pv.filename),
        target_data,
        target_image.filename or "target.jpg",
        target_image.content_type or _guess_mime(target_image.filename, "image/jpeg"),
        type_gen,
    )
    return JSONResponse(_envelope_ok(result))


# ---- short videos ----------------------------------------------------------


@router.get("/short-videos/templates")
async def short_video_templates() -> JSONResponse:
    now = time.time()
    cached = _templates_cache.get("data")
    fetched_at = _templates_cache.get("fetched_at", 0.0) or 0.0
    if cached is not None and (now - fetched_at) < _TEMPLATES_TTL_SEC:
        return JSONResponse(_envelope_ok(cached))
    try:
        data = await _co_get(CO_SHORT_TEMPLATES)
        _templates_cache["data"] = data
        _templates_cache["fetched_at"] = now
        return JSONResponse(_envelope_ok(data))
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code, content=_envelope_err(str(exc.detail))
        )


@router.post("/short-videos/generate")
async def short_video_generate(
    target_image: UploadFile = File(...),
) -> JSONResponse:
    """Fire-and-forget submission. Results trickle in via webhook to
    RESULTS_DIR/short_videos/{batchId}/{undressingId}.mp4. Client polls
    /short-videos/status/{batchId}.
    """
    _require_key()
    data_bytes = await target_image.read()
    if not data_bytes:
        raise HTTPException(status_code=400, detail="target_image required")
    if len(data_bytes) > 60 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="target_image too large (max 60MB)")

    id_gen = uuid.uuid4().hex
    files = {
        "target_image": (
            target_image.filename or "face.jpg",
            data_bytes,
            target_image.content_type or _guess_mime(target_image.filename, "image/jpeg"),
        )
    }
    form = {"id_gen": id_gen, "webhook": WEBHOOK_URL}

    try:
        submit = await _co_post_multipart(CO_SHORT_GENERATE, files, form)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code, content=_envelope_err(str(exc.detail))
        )

    logger.info(
        "[clothoff/short-videos] submitted id_gen=%s batchId=%s size=%s",
        id_gen, submit.get("batchId"), submit.get("batchSize"),
    )
    return JSONResponse(_envelope_ok(submit))


@router.get("/short-videos/status/{batch_id}")
async def short_video_status(batch_id: str) -> JSONResponse:
    try:
        data = await _co_get(f"{CO_SHORT_STATUS}/{batch_id}")
        return JSONResponse(_envelope_ok(data))
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code, content=_envelope_err(str(exc.detail))
        )


# ---- webhook ---------------------------------------------------------------


async def _process_webhook(request: Request) -> Response:
    """Shared implementation for both signed and legacy webhook endpoints.

    Extracted so the verified route and the legacy route can share body
    parsing + state mutation without code duplication. Always returns 200
    regardless of outcome — any non-200 would cause ClothOff to retry,
    which we never want.
    """
    try:
        form = await request.form()
        field_names = list(form.keys())
        id_gen = (form.get("undressingId") or form.get("id_gen") or "").strip()
        batch_id = (form.get("batchId") or "").strip()
        error_msg = form.get("error") or ""

        logger.info(
            "[clothoff/webhook] received id_gen=%s batchId=%s has_error=%s fields=%s",
            id_gen, batch_id, bool(error_msg), field_names,
        )

        # Extract binary payload
        res_image = form.get("image")
        res_video = form.get("video")
        res_pv = form.get("result_pv")

        async def _read_upload(obj: Any) -> Optional[bytes]:
            if obj is None:
                return None
            if hasattr(obj, "read"):
                try:
                    return await obj.read()
                except Exception as exc:
                    logger.warning("[clothoff/webhook] read failed: %s", exc)
                    return None
            if isinstance(obj, (bytes, bytearray)):
                return bytes(obj)
            return None

        # Short-video batch callback: persist directly, don't touch futures
        if batch_id:
            batch_dir = os.path.join(RESULTS_DIR, "short_videos", batch_id)
            os.makedirs(batch_dir, exist_ok=True)
            payload = await _read_upload(res_video) or await _read_upload(res_pv) \
                or await _read_upload(res_image)
            if payload is None:
                logger.warning(
                    "[clothoff/webhook] short-video batch %s id=%s has no payload",
                    batch_id, id_gen,
                )
                return Response(status_code=200)
            ext = "mp4"
            if res_image is not None and res_video is None:
                ext = "png"
            fname = f"{id_gen or uuid.uuid4().hex}.{ext}"
            fpath = os.path.join(batch_dir, fname)
            try:
                with open(fpath, "wb") as fh:
                    fh.write(payload)
                logger.info(
                    "[clothoff/webhook] saved short-video %s/%s (%d bytes)",
                    batch_id, fname, len(payload),
                )
            except OSError as exc:
                logger.error("[clothoff/webhook] save failed: %s", exc)
            return Response(status_code=200)

        # Regular future-based callback
        if not id_gen:
            logger.warning("[clothoff/webhook] missing id_gen and no batchId")
            return Response(status_code=200)

        future = _clothoff_futures.get(id_gen)
        if future is None:
            logger.warning(
                "[clothoff/webhook] unknown id_gen=%s (already resolved/timed out)",
                id_gen,
            )
            return Response(status_code=200)
        if future.done():
            logger.warning("[clothoff/webhook] future already done id_gen=%s", id_gen)
            return Response(status_code=200)

        if error_msg:
            logger.error("[clothoff/webhook] upstream error id_gen=%s: %s", id_gen, error_msg)
            future.set_exception(
                HTTPException(status_code=502, detail=f"ClothOff processing failed: {error_msg}")
            )
            return Response(status_code=200)

        video_bytes = await _read_upload(res_video) or await _read_upload(res_pv)
        if video_bytes and res_video is not None:
            future.set_result((video_bytes, "video"))
            return Response(status_code=200)

        image_bytes = await _read_upload(res_image) or await _read_upload(res_pv)
        if image_bytes and res_image is not None:
            future.set_result((image_bytes, "image"))
            return Response(status_code=200)

        # result_pv fallback (type unknown — default to video if from animate/face-swap)
        if res_pv is not None:
            pv_bytes = await _read_upload(res_pv)
            if pv_bytes:
                kind = "video" if (res_pv.filename or "").lower().endswith(
                    (".mp4", ".webm", ".mov")
                ) else "image"
                future.set_result((pv_bytes, kind))
                return Response(status_code=200)

        logger.error(
            "[clothoff/webhook] no payload found for id_gen=%s fields=%s",
            id_gen, field_names,
        )
        future.set_exception(HTTPException(status_code=502, detail="No result returned"))
    except Exception as exc:  # never raise from webhook
        logger.exception("[clothoff/webhook] unhandled error: %s", exc)

    return Response(status_code=200)


@router.post("/webhook/{secret}")
async def clothoff_webhook_signed(secret: str, request: Request) -> Response:
    """Verified webhook endpoint — the one we actually give to ClothOff.

    The secret is baked into WEBHOOK_URL at submit time. We compare it
    against `CLOTHOFF_WEBHOOK_SECRET` via `hmac.compare_digest` for
    timing-safe equality. Mismatches drop to 200 silently — returning
    403 would let an attacker probe for the secret via timing or error
    messages. Logging is deliberately minimal to avoid leaking the
    secret into gateway.log even on mismatch.
    """
    import hmac
    expected = CLOTHOFF_WEBHOOK_SECRET
    if not expected:
        # No secret configured → fall through to legacy handler so
        # existing deployments still work during the rolling upgrade.
        return await _process_webhook(request)
    if not hmac.compare_digest(secret, expected):
        logger.warning(
            "[clothoff/webhook/signed] rejected — secret mismatch, client=%s",
            request.client.host if request.client else "?",
        )
        return Response(status_code=200)  # silent drop
    return await _process_webhook(request)


@router.post("/webhook")
async def clothoff_webhook_legacy(request: Request) -> Response:
    """Unsigned webhook endpoint — kept for backwards compatibility.

    When `CLOTHOFF_WEBHOOK_SECRET` is set, we now expect all callbacks
    to land at `/webhook/{secret}`. Unsigned hits from anywhere are
    silently dropped (200 OK) so attackers who guess a valid id_gen
    can't inject fake results. When the secret is unset (dev / legacy
    deploys) we fall through to the real handler so nothing breaks.
    """
    if CLOTHOFF_WEBHOOK_SECRET:
        logger.warning(
            "[clothoff/webhook/legacy] rejected — unsigned hit while secret is configured, client=%s",
            request.client.host if request.client else "?",
        )
        return Response(status_code=200)  # silent drop
    return await _process_webhook(request)


# ---- results file serving --------------------------------------------------


@router.get("/results/{filename:path}")
async def get_result(filename: str) -> Response:
    # Prevent path traversal
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    filepath = os.path.join(RESULTS_DIR, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Result not found")
    return FileResponse(filepath, media_type=_content_type_for(filename))
