"""Third-party video generation API proxy.

Routes requests to external providers (Alibaba Wan2.6, BytePlus Seedance,
Seedance 2.0 via OpenGW) and normalises responses for the frontend.
"""

import logging
import uuid
from typing import Optional

import aiohttp
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api_gateway.dependencies import get_config
from api_gateway.config import GatewayConfig
from api_gateway.services.task_store import TaskStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["thirdparty"])

TIMEOUT = aiohttp.ClientTimeout(total=30)


# ---------------------------------------------------------------------------
# Shared response models
# ---------------------------------------------------------------------------

class ThirdPartySubmitResponse(BaseModel):
    success: bool
    task_id: Optional[str] = None
    task_status: Optional[str] = None
    provider: Optional[str] = None
    error: Optional[str] = None


class ThirdPartyQueryResponse(BaseModel):
    success: bool
    task_id: str
    task_status: str
    video_url: Optional[str] = None
    error_message: Optional[str] = None
    provider: Optional[str] = None


# =========================================================================
# Wan2.6 (Alibaba DashScope)
# =========================================================================

class Wan26T2VRequest(BaseModel):
    model: str = "wan2.6-t2v"
    prompt: str
    negative_prompt: Optional[str] = None
    duration: int = 5
    size: str = "1280*720"
    shot_type: Optional[str] = None
    prompt_extend: bool = True
    audio_url: Optional[str] = None
    seed: Optional[int] = None


class Wan26I2VRequest(BaseModel):
    model: str = "wan2.6-i2v"
    image: str
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    duration: int = 5
    resolution: str = "720P"
    shot_type: Optional[str] = None
    prompt_extend: bool = True
    audio_url: Optional[str] = None
    seed: Optional[int] = None


@router.post("/thirdparty/wan26/text-to-video", response_model=ThirdPartySubmitResponse)
async def wan26_text_to_video(
    req: Wan26T2VRequest,
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Wan2.6 text-to-video via Alibaba DashScope."""
    payload = {
        "model": req.model,
        "input": {"prompt": req.prompt},
        "parameters": {
            "size": req.size,
            "duration": req.duration,
            "prompt_extend": req.prompt_extend,
            "watermark": False,
        },
    }
    if req.negative_prompt:
        payload["input"]["negative_prompt"] = req.negative_prompt
    if req.audio_url:
        payload["input"]["audio_url"] = req.audio_url
    if req.shot_type:
        payload["parameters"]["shot_type"] = req.shot_type
    if req.seed is not None:
        payload["parameters"]["seed"] = req.seed
    result = await _wan26_submit(payload, config)
    if result.success:
        task_store: TaskStore = request.app.state.task_store
        await task_store.create(
            task_id=result.task_id or uuid.uuid4().hex,
            task_type="wan26_t2v",
            category="thirdparty",
            provider="wan26",
            prompt=req.prompt,
            model=req.model,
            external_task_id=result.task_id,
        )
    return result


@router.post("/thirdparty/wan26/image-to-video", response_model=ThirdPartySubmitResponse)
async def wan26_image_to_video(
    req: Wan26I2VRequest,
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Wan2.6 image-to-video via Alibaba DashScope."""
    payload = {
        "model": req.model,
        "input": {"img_url": req.image},
        "parameters": {
            "resolution": req.resolution,
            "duration": req.duration,
            "prompt_extend": req.prompt_extend,
            "watermark": False,
        },
    }
    if req.prompt:
        payload["input"]["prompt"] = req.prompt
    if req.negative_prompt:
        payload["input"]["negative_prompt"] = req.negative_prompt
    if req.audio_url:
        payload["input"]["audio_url"] = req.audio_url
    if req.shot_type:
        payload["parameters"]["shot_type"] = req.shot_type
    if req.seed is not None:
        payload["parameters"]["seed"] = req.seed
    result = await _wan26_submit(payload, config)
    if result.success:
        task_store: TaskStore = request.app.state.task_store
        await task_store.create(
            task_id=result.task_id or uuid.uuid4().hex,
            task_type="wan26_i2v",
            category="thirdparty",
            provider="wan26",
            prompt=req.prompt,
            model=req.model,
            external_task_id=result.task_id,
        )
    return result


async def _wan26_submit(payload: dict, config: GatewayConfig) -> ThirdPartySubmitResponse:
    headers = {
        "Authorization": f"Bearer {config.wan26_api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(config.wan26_api_url, json=payload, headers=headers) as resp:
                body = await resp.json()
                logger.info("[Wan2.6] status=%s body=%s", resp.status, str(body)[:200])
                if resp.status == 200:
                    output = body.get("output", {})
                    return ThirdPartySubmitResponse(
                        success=True,
                        task_id=output.get("task_id"),
                        task_status=output.get("task_status"),
                        provider="wan26",
                    )
                else:
                    detail = body.get("message", "") or str(body)[:200]
                    return ThirdPartySubmitResponse(
                        success=False,
                        error=f"Wan2.6 API error ({resp.status}): {detail}",
                        provider="wan26",
                    )
    except Exception as exc:
        logger.exception("[Wan2.6 Submit] error")
        return ThirdPartySubmitResponse(success=False, error=str(exc), provider="wan26")


@router.get("/thirdparty/wan26/tasks/{task_id}", response_model=ThirdPartyQueryResponse)
async def wan26_query_task(
    task_id: str,
    config: GatewayConfig = Depends(get_config),
):
    """Query a Wan2.6 task status."""
    url = f"{config.wan26_api_url}/{task_id}"
    headers = {"Authorization": f"Bearer {config.wan26_api_key}"}
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(url, headers=headers) as resp:
                body = await resp.json()
                if resp.status == 200:
                    output = body.get("output", {})
                    task_status = output.get("task_status", "UNKNOWN")
                    video_url = None
                    if task_status == "SUCCEEDED":
                        video_url = output.get("video_url")
                    error_msg = None
                    if task_status == "FAILED":
                        error_msg = output.get("message", "Task failed")
                    return ThirdPartyQueryResponse(
                        success=True,
                        task_id=task_id,
                        task_status=task_status,
                        video_url=video_url,
                        error_message=error_msg,
                        provider="wan26",
                    )
                else:
                    detail = body.get("message", "") or str(body)[:200]
                    return ThirdPartyQueryResponse(
                        success=False,
                        task_id=task_id,
                        task_status="UNKNOWN",
                        error_message=f"Query failed ({resp.status}): {detail}",
                        provider="wan26",
                    )
    except Exception as exc:
        logger.exception("[Wan2.6 Query] error")
        return ThirdPartyQueryResponse(
            success=False, task_id=task_id, task_status="UNKNOWN",
            error_message=str(exc), provider="wan26",
        )


# =========================================================================
# Seedance (BytePlus)
# =========================================================================

class SeedanceT2VRequest(BaseModel):
    prompt: str
    model: str = "seedance-1-5-pronew"
    duration: Optional[int] = 5
    resolution: Optional[str] = "720P"


class SeedanceI2VRequest(BaseModel):
    image: str
    prompt: Optional[str] = None
    model: str = "seedance-1-0-pro-250528"
    duration: Optional[int] = 5
    resolution: Optional[str] = "720p"


@router.post("/thirdparty/seedance/text-to-video", response_model=ThirdPartySubmitResponse)
async def seedance_text_to_video(
    req: SeedanceT2VRequest,
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Seedance text-to-video via BytePlus."""
    payload = {
        "model_id": req.model,
        "content": [{"type": "text", "text": req.prompt}],
        "parameters": {"duration": req.duration, "resolution": req.resolution},
    }
    result = await _seedance_submit(payload, config)
    if result.success:
        task_store: TaskStore = request.app.state.task_store
        await task_store.create(
            task_id=result.task_id or uuid.uuid4().hex,
            task_type="seedance_t2v",
            category="thirdparty",
            provider="seedance",
            prompt=req.prompt,
            model=req.model,
            external_task_id=result.task_id,
        )
    return result


@router.post("/thirdparty/seedance/image-to-video", response_model=ThirdPartySubmitResponse)
async def seedance_image_to_video(
    req: SeedanceI2VRequest,
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Seedance image-to-video via BytePlus."""
    content = [{"type": "image_url", "image_url": {"url": req.image}}]
    if req.prompt:
        content.append({"type": "text", "text": req.prompt})
    payload = {
        "model_id": req.model,
        "content": content,
        "parameters": {"duration": req.duration, "resolution": req.resolution},
    }
    result = await _seedance_submit(payload, config)
    if result.success:
        task_store: TaskStore = request.app.state.task_store
        await task_store.create(
            task_id=result.task_id or uuid.uuid4().hex,
            task_type="seedance_i2v",
            category="thirdparty",
            provider="seedance",
            prompt=req.prompt,
            model=req.model,
            external_task_id=result.task_id,
        )
    return result


async def _seedance_submit(payload: dict, config: GatewayConfig) -> ThirdPartySubmitResponse:
    headers = {
        "Authorization": f"Bearer {config.byteplus_api_key}",
        "Content-Type": "application/json",
    }
    # Seedance uses a sub-path under the base BytePlus API URL
    url = f"{config.byteplus_api_url}/contents/generations/tasks"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                logger.info("[Seedance] status=%s body=%s", resp.status, str(body)[:200])
                if resp.status == 200:
                    return ThirdPartySubmitResponse(
                        success=True,
                        task_id=body.get("id"),
                        task_status=body.get("status", "PENDING").upper(),
                        provider="seedance",
                    )
                else:
                    detail = body.get("message", "") or str(body)[:200]
                    return ThirdPartySubmitResponse(
                        success=False,
                        error=f"Seedance API error ({resp.status}): {detail}",
                        provider="seedance",
                    )
    except Exception as exc:
        logger.exception("[Seedance Submit] error")
        return ThirdPartySubmitResponse(success=False, error=str(exc), provider="seedance")


@router.get("/thirdparty/seedance/tasks/{task_id}", response_model=ThirdPartyQueryResponse)
async def seedance_query_task(
    task_id: str,
    config: GatewayConfig = Depends(get_config),
):
    """Query a Seedance task status."""
    url = f"{config.byteplus_api_url}/contents/generations/tasks/{task_id}"
    headers = {"Authorization": f"Bearer {config.byteplus_api_key}"}
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(url, headers=headers) as resp:
                body = await resp.json()
                if resp.status == 200:
                    raw_status = body.get("status", "").lower()
                    status_map = {
                        "pending": "PENDING",
                        "running": "RUNNING",
                        "succeeded": "SUCCEEDED",
                        "failed": "FAILED",
                    }
                    task_status = status_map.get(raw_status, "UNKNOWN")
                    video_url = None
                    if raw_status == "succeeded" and "content" in body:
                        video_url = body["content"].get("video_url")
                    error_msg = None
                    if raw_status == "failed" and "error" in body:
                        err = body["error"]
                        error_msg = f"{err.get('code', 'Error')}: {err.get('message', 'Unknown')}"
                    return ThirdPartyQueryResponse(
                        success=True,
                        task_id=task_id,
                        task_status=task_status,
                        video_url=video_url,
                        error_message=error_msg,
                        provider="seedance",
                    )
                else:
                    return ThirdPartyQueryResponse(
                        success=False,
                        task_id=task_id,
                        task_status="UNKNOWN",
                        error_message=f"Query failed: {resp.status}",
                        provider="seedance",
                    )
    except Exception as exc:
        logger.exception("[Seedance Query] error")
        return ThirdPartyQueryResponse(
            success=False, task_id=task_id, task_status="UNKNOWN",
            error_message=str(exc), provider="seedance",
        )


# =========================================================================
# Seedance 2.0 / Romance 2.0 (OpenGW — unified multi-modal interface)
# =========================================================================

class Seedance2T2VRequest(BaseModel):
    prompt: str
    model: str = "doubao-seedance-2-0-fast-260128"
    duration: Optional[int] = 5
    ratio: str = "16:9"
    resolution: str = "720p"
    generate_audio: bool = True


class Seedance2I2VRequest(BaseModel):
    image: str  # URL or base64
    prompt: Optional[str] = None
    model: str = "doubao-seedance-2-0-fast-260128"
    duration: Optional[int] = 5
    ratio: str = "adaptive"
    resolution: str = "720p"
    generate_audio: bool = True


class Seedance2MultiModalRequest(BaseModel):
    """Multi-modal request: text + images + videos + audio references."""
    prompt: Optional[str] = None
    model: str = "doubao-seedance-2-0-fast-260128"
    duration: Optional[int] = 5
    ratio: str = "16:9"
    resolution: str = "720p"
    generate_audio: bool = True
    image_urls: Optional[list] = None       # [{"url": "...", "role": "first_frame|reference_image"}]
    video_urls: Optional[list] = None       # [{"url": "...", "role": "reference_video"}]
    audio_urls: Optional[list] = None       # [{"url": "...", "role": "reference_audio"}]


@router.post("/thirdparty/seedance2/text-to-video", response_model=ThirdPartySubmitResponse)
async def seedance2_text_to_video(
    req: Seedance2T2VRequest,
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Seedance 2.0 / Romance 2.0 text-to-video via OpenGW."""
    payload = {
        "model": req.model,
        "content": [{"type": "text", "text": req.prompt}],
        "metadata": {
            "duration": req.duration,
            "ratio": req.ratio,
            "resolution": req.resolution,
            "watermark": False,
            "generate_audio": req.generate_audio,
        },
    }
    result = await _seedance2_submit(payload, config)
    if result.success:
        task_store: TaskStore = request.app.state.task_store
        await task_store.create(
            task_id=result.task_id or uuid.uuid4().hex,
            task_type="seedance2_t2v",
            category="thirdparty",
            provider="seedance2",
            prompt=req.prompt,
            model=req.model,
            external_task_id=result.task_id,
        )
    return result


@router.post("/thirdparty/seedance2/image-to-video", response_model=ThirdPartySubmitResponse)
async def seedance2_image_to_video(
    req: Seedance2I2VRequest,
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Seedance 2.0 / Romance 2.0 image-to-video via OpenGW."""
    is_romance = req.model.lower().startswith("romance")
    if is_romance:
        # Romance uses flat request body
        payload = {
            "model": req.model,
            "image": req.image,
            "prompt": req.prompt or "",
            "duration": req.duration,
            "size": req.resolution,
        }
    else:
        # Seedance uses content array + metadata
        content = [
            {
                "type": "image_url",
                "image_url": {"url": req.image},
                "role": "first_frame",
            },
        ]
        if req.prompt:
            content.insert(0, {"type": "text", "text": req.prompt})
        payload = {
            "model": req.model,
            "content": content,
            "metadata": {
                "duration": req.duration,
                "ratio": req.ratio,
                "resolution": req.resolution,
                "watermark": False,
                "generate_audio": req.generate_audio,
            },
        }
    result = await _seedance2_submit(payload, config)
    if result.success:
        task_store: TaskStore = request.app.state.task_store
        await task_store.create(
            task_id=result.task_id or uuid.uuid4().hex,
            task_type="seedance2_i2v",
            category="thirdparty",
            provider="seedance2",
            prompt=req.prompt,
            model=req.model,
            external_task_id=result.task_id,
        )
    return result


@router.post("/thirdparty/seedance2/multi-modal", response_model=ThirdPartySubmitResponse)
async def seedance2_multi_modal(
    req: Seedance2MultiModalRequest,
    request: Request,
    config: GatewayConfig = Depends(get_config),
):
    """Seedance 2.0 / Romance 2.0 multi-modal video generation via OpenGW."""
    content = []
    if req.prompt:
        content.append({"type": "text", "text": req.prompt})
    for img in (req.image_urls or []):
        content.append({
            "type": "image_url",
            "image_url": {"url": img["url"]},
            "role": img.get("role", "reference_image"),
        })
    for vid in (req.video_urls or []):
        content.append({
            "type": "video_url",
            "video_url": {"url": vid["url"]},
            "role": vid.get("role", "reference_video"),
        })
    for aud in (req.audio_urls or []):
        content.append({
            "type": "audio_url",
            "audio_url": {"url": aud["url"]},
            "role": aud.get("role", "reference_audio"),
        })
    payload = {
        "model": req.model,
        "content": content,
        "metadata": {
            "duration": req.duration,
            "ratio": req.ratio,
            "resolution": req.resolution,
            "watermark": False,
            "generate_audio": req.generate_audio,
        },
    }
    result = await _seedance2_submit(payload, config)
    if result.success:
        task_store: TaskStore = request.app.state.task_store
        await task_store.create(
            task_id=result.task_id or uuid.uuid4().hex,
            task_type="seedance2_mm",
            category="thirdparty",
            provider="seedance2",
            prompt=req.prompt,
            model=req.model,
            external_task_id=result.task_id,
        )
    return result


async def _seedance2_submit(payload: dict, config: GatewayConfig) -> ThirdPartySubmitResponse:
    headers = {
        "Authorization": f"Bearer {config.seedance2_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{config.seedance2_api_url}/v1/video/generations"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                logger.info("[Seedance2/OpenGW] status=%s body=%s", resp.status, str(body)[:300])
                if resp.status == 200:
                    return ThirdPartySubmitResponse(
                        success=True,
                        task_id=body.get("id"),
                        task_status=body.get("status", "PENDING").upper(),
                        provider="seedance2",
                    )
                else:
                    detail = body.get("error", {}).get("message", "") or body.get("message", "") or str(body)[:200]
                    return ThirdPartySubmitResponse(
                        success=False,
                        error=f"OpenGW API error ({resp.status}): {detail}",
                        provider="seedance2",
                    )
    except Exception as exc:
        logger.exception("[Seedance2/OpenGW Submit] error")
        return ThirdPartySubmitResponse(success=False, error=str(exc), provider="seedance2")


@router.get("/thirdparty/seedance2/tasks/{task_id}", response_model=ThirdPartyQueryResponse)
async def seedance2_query_task(
    task_id: str,
    config: GatewayConfig = Depends(get_config),
):
    """Query a Seedance 2.0 / Romance 2.0 task status."""
    url = f"{config.seedance2_api_url}/v1/videos/{task_id}"
    headers = {"Authorization": f"Bearer {config.seedance2_api_key}"}
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(url, headers=headers) as resp:
                body = await resp.json()
                if resp.status == 200:
                    raw_status = body.get("status", "").lower()
                    status_map = {
                        "queued": "PENDING",
                        "pending": "PENDING",
                        "running": "RUNNING",
                        "in_progress": "RUNNING",
                        "succeeded": "SUCCEEDED",
                        "completed": "SUCCEEDED",
                        "failed": "FAILED",
                    }
                    task_status = status_map.get(raw_status, "UNKNOWN")
                    video_url = None
                    if task_status == "SUCCEEDED":
                        video_url = body.get("metadata", {}).get("url")
                    error_msg = None
                    if task_status == "FAILED":
                        err = body.get("error", {})
                        if isinstance(err, dict):
                            error_msg = f"{err.get('code', 'Error')}: {err.get('message', 'Unknown')}"
                        else:
                            error_msg = str(err) or "Task failed"
                    return ThirdPartyQueryResponse(
                        success=True,
                        task_id=task_id,
                        task_status=task_status,
                        video_url=video_url,
                        error_message=error_msg,
                        provider="seedance2",
                    )
                else:
                    return ThirdPartyQueryResponse(
                        success=False,
                        task_id=task_id,
                        task_status="UNKNOWN",
                        error_message=f"Query failed: {resp.status}",
                        provider="seedance2",
                    )
    except Exception as exc:
        logger.exception("[Seedance2/OpenGW Query] error")
        return ThirdPartyQueryResponse(
            success=False, task_id=task_id, task_status="UNKNOWN",
            error_message=str(exc), provider="seedance2",
        )
