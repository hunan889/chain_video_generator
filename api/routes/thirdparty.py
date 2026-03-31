"""
Third-party video generation API proxy.

Routes requests to external providers (Alibaba Wan2.6, BytePlus Seedance)
and normalises responses for the frontend.
"""

import logging
from typing import Optional

import aiohttp
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.config import (
    WAN26_API_KEY, WAN26_API_URL,
    BYTEPLUS_API_KEY, BYTEPLUS_API_URL,
)
from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()

TIMEOUT = aiohttp.ClientTimeout(total=30)


# ---------------------------------------------------------------------------
# Shared response model
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
# Wan2.6 (Alibaba DashScope / Cloudwise)
# =========================================================================

class Wan26T2VRequest(BaseModel):
    model: str = "wan2.6-t2v"
    prompt: str
    negative_prompt: Optional[str] = None
    duration: int = 5
    size: str = "1280*720"
    shot_type: Optional[str] = None
    prompt_extend: bool = True
    audio_url: Optional[str] = None    # enable audio (pass "auto" for auto-generated)
    seed: Optional[int] = None

class Wan26I2VRequest(BaseModel):
    model: str = "wan2.6-i2v"
    image: str                         # base64 data URL
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    duration: int = 5
    resolution: str = "720P"
    shot_type: Optional[str] = None
    prompt_extend: bool = True
    audio_url: Optional[str] = None
    seed: Optional[int] = None


@router.post("/thirdparty/wan26/text-to-video", response_model=ThirdPartySubmitResponse)
async def wan26_text_to_video(req: Wan26T2VRequest, _=Depends(verify_api_key)):
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
    if req.shot_type and "2.6" in req.model:
        payload["parameters"]["shot_type"] = req.shot_type
    if req.seed is not None:
        payload["parameters"]["seed"] = req.seed

    return await _wan26_submit(payload)


@router.post("/thirdparty/wan26/image-to-video", response_model=ThirdPartySubmitResponse)
async def wan26_image_to_video(req: Wan26I2VRequest, _=Depends(verify_api_key)):
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
    if req.shot_type and "2.6" in req.model:
        payload["parameters"]["shot_type"] = req.shot_type
    if req.seed is not None:
        payload["parameters"]["seed"] = req.seed

    return await _wan26_submit(payload)


async def _wan26_submit(payload: dict) -> ThirdPartySubmitResponse:
    """Send a generation request to the Wan2.6 API."""
    headers = {
        "Authorization": f"Bearer {WAN26_API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(WAN26_API_URL, json=payload, headers=headers) as resp:
                body = await resp.json()
                logger.info(f"[Wan2.6] status={resp.status} body={str(body)[:300]}")
                if resp.status == 200:
                    output = body.get("output", {})
                    return ThirdPartySubmitResponse(
                        success=True,
                        task_id=output.get("task_id"),
                        task_status=output.get("task_status", "PENDING"),
                        provider="wan26",
                    )
                else:
                    return ThirdPartySubmitResponse(
                        success=False,
                        error=f"Wan2.6 API {resp.status}: {body.get('message', resp.reason)}",
                        provider="wan26",
                    )
    except Exception as e:
        logger.exception("[Wan2.6] submit error")
        return ThirdPartySubmitResponse(success=False, error=str(e), provider="wan26")


@router.get("/thirdparty/wan26/tasks/{task_id}", response_model=ThirdPartyQueryResponse)
async def wan26_query_task(task_id: str, _=Depends(verify_api_key)):
    """Query Wan2.6 task status."""
    # Derive task query URL from submission URL (same host)
    # e.g. https://api.cloudwise.ai/api/v1/services/... → https://api.cloudwise.ai/api/v1/tasks/{id}
    base = WAN26_API_URL.rsplit("/services/", 1)[0] + "/tasks"
    query_url = f"{base}/{task_id}"
    headers = {
        "Authorization": f"Bearer {WAN26_API_KEY}",
        "X-DashScope-Async": "enable",
    }
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(query_url, headers=headers) as resp:
                body = await resp.json()
                logger.info(f"[Wan2.6 Query] status={resp.status} body={str(body)[:500]}")

                # Extract output — may be nested under "data"
                data = body.get("data", body)
                output = data.get("output", data)
                task_status = output.get("task_status", "UNKNOWN")

                if task_status in ("SUCCEEDED", "FAILED", "PENDING", "RUNNING"):
                    video_url = output.get("video_url") if task_status == "SUCCEEDED" else None
                    error_msg = None
                    if task_status == "FAILED":
                        code = output.get("code", "")
                        msg = output.get("message", "")
                        error_msg = f"[{code}] {msg}" if code else (msg or "Task failed")
                    return ThirdPartyQueryResponse(
                        success=True,
                        task_id=task_id,
                        task_status=task_status,
                        video_url=video_url,
                        error_message=error_msg,
                        provider="wan26",
                    )
                elif resp.status == 200:
                    return ThirdPartyQueryResponse(
                        success=True,
                        task_id=task_id,
                        task_status=task_status,
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
    except Exception as e:
        logger.exception("[Wan2.6 Query] error")
        return ThirdPartyQueryResponse(
            success=False, task_id=task_id, task_status="UNKNOWN",
            error_message=str(e), provider="wan26",
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
    image: str                         # base64 data URL
    prompt: Optional[str] = None
    model: str = "seedance-1-0-pro-250528"
    duration: Optional[int] = 5
    resolution: Optional[str] = "720p"


@router.post("/thirdparty/seedance/text-to-video", response_model=ThirdPartySubmitResponse)
async def seedance_text_to_video(req: SeedanceT2VRequest, _=Depends(verify_api_key)):
    """BytePlus Seedance text-to-video."""
    payload = {
        "model": req.model,
        "content": [{"type": "text", "text": req.prompt}],
    }
    params = {}
    if req.duration:
        params["duration"] = req.duration
    if req.resolution:
        params["resolution"] = req.resolution
    if params:
        payload["parameters"] = params

    return await _seedance_submit(payload)


@router.post("/thirdparty/seedance/image-to-video", response_model=ThirdPartySubmitResponse)
async def seedance_image_to_video(req: SeedanceI2VRequest, _=Depends(verify_api_key)):
    """BytePlus Seedance image-to-video."""
    content = []
    if req.prompt:
        content.append({"type": "text", "text": req.prompt})
    content.append({"type": "image_url", "image_url": {"url": req.image}})

    payload = {
        "model": req.model,
        "content": content,
    }
    if req.duration:
        payload["duration"] = req.duration
    if req.resolution:
        payload["resolution"] = req.resolution.lower()

    return await _seedance_submit(payload)


async def _seedance_submit(payload: dict) -> ThirdPartySubmitResponse:
    """Send a generation request to the BytePlus Seedance API."""
    headers = {
        "Authorization": f"Bearer {BYTEPLUS_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{BYTEPLUS_API_URL}/contents/generations/tasks"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                logger.info(f"[Seedance] status={resp.status} body={str(body)[:300]}")
                if resp.status == 200:
                    task_id = body.get("id")
                    if task_id:
                        return ThirdPartySubmitResponse(
                            success=True,
                            task_id=task_id,
                            task_status=body.get("status", "queued").upper(),
                            provider="seedance",
                        )
                    return ThirdPartySubmitResponse(
                        success=False, error="No task ID returned", provider="seedance",
                    )
                else:
                    return ThirdPartySubmitResponse(
                        success=False,
                        error=f"Seedance API {resp.status}: {body.get('message', resp.reason)}",
                        provider="seedance",
                    )
    except Exception as e:
        logger.exception("[Seedance] submit error")
        return ThirdPartySubmitResponse(success=False, error=str(e), provider="seedance")


@router.get("/thirdparty/seedance/tasks/{task_id}", response_model=ThirdPartyQueryResponse)
async def seedance_query_task(task_id: str, _=Depends(verify_api_key)):
    """Query BytePlus Seedance task status."""
    headers = {
        "Authorization": f"Bearer {BYTEPLUS_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{BYTEPLUS_API_URL}/contents/generations/tasks/{task_id}"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(url, headers=headers) as resp:
                body = await resp.json()
                logger.info(f"[Seedance Query] status={resp.status}")
                if resp.status == 200:
                    raw_status = body.get("status", "unknown")
                    status_map = {
                        "queued": "PENDING",
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
    except Exception as e:
        logger.exception("[Seedance Query] error")
        return ThirdPartyQueryResponse(
            success=False, task_id=task_id, task_status="UNKNOWN",
            error_message=str(e), provider="seedance",
        )
