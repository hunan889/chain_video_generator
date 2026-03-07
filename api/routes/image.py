"""SeedDream image generation via BytePlus API."""

import base64
import logging
import math
import os
import requests as http_requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from typing import List, Optional

from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()

BYTEPLUS_API_KEY = os.getenv(
    "BYTEPLUS_API_KEY", "f3cb7588-0af7-4753-96c4-8ca992600bca"
)
BYTEPLUS_ENDPOINT = "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations"
SEEDREAM_MODEL = "seedream-4-5-251128"
MIN_PIXELS = 3686400  # ~1920x1920

SIZE_PRESETS = {
    "1K": "1024x1024",
    "2K": "2048x2048",
    "4K": "4096x4096",
}


def _ensure_min_size(width: int, height: int) -> tuple[int, int]:
    """Scale up dimensions to meet minimum pixel requirement, preserving aspect ratio."""
    pixels = width * height
    if pixels >= MIN_PIXELS:
        return width, height
    scale = math.sqrt(MIN_PIXELS / pixels)
    width = math.ceil(width * scale)
    height = math.ceil(height * scale)
    width = math.ceil(width / 64) * 64
    height = math.ceil(height / 64) * 64
    return width, height


def _parse_size(size: str) -> str:
    """Resolve size presets and enforce minimum pixel count."""
    size = SIZE_PRESETS.get(size.upper(), size) if size else "2048x2048"
    try:
        w, h = map(int, size.lower().split("x"))
    except (ValueError, AttributeError):
        raise HTTPException(400, f"Invalid size format: {size!r}. Use 'WIDTHxHEIGHT' or preset (1K/2K/4K).")
    w, h = _ensure_min_size(w, h)
    return f"{w}x{h}"


def _call_byteplus(payload: dict) -> str:
    """Send request to BytePlus API and return the result image URL."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BYTEPLUS_API_KEY}",
    }
    try:
        resp = http_requests.post(BYTEPLUS_ENDPOINT, json=payload, headers=headers, timeout=120)
    except http_requests.RequestException as e:
        logger.error("BytePlus API request failed: %s", e)
        raise HTTPException(502, f"BytePlus API request failed: {e}")

    if resp.status_code != 200:
        logger.error("BytePlus API error %d: %s", resp.status_code, resp.text[:500])
        raise HTTPException(resp.status_code, f"BytePlus API error: {resp.text[:500]}")

    try:
        data = resp.json()
        return data["data"][0]["url"]
    except (KeyError, IndexError, ValueError) as e:
        logger.error("Unexpected BytePlus response: %s", resp.text[:500])
        raise HTTPException(502, f"Unexpected API response format: {e}")


# --- Text-to-Image ---

class ImageRequest(BaseModel):
    prompt: str = Field(..., description="Text prompt for image generation")
    size: Optional[str] = Field("2048x2048", description="Image size: '1K', '2K', '4K' or 'WIDTHxHEIGHT'")
    seed: Optional[int] = Field(None, description="Random seed for reproducibility")
    negative_prompt: Optional[str] = Field(None, description="Negative prompt (accepted but not used by API)")


class ImageResponse(BaseModel):
    url: str
    size: str
    seed: Optional[int] = None


@router.post("/image/generate", response_model=ImageResponse)
async def generate_image(req: ImageRequest, _user=Depends(verify_api_key)):
    """Generate an image using BytePlus SeedDream API (text-to-image)."""
    size = _parse_size(req.size)
    payload = {
        "model": SEEDREAM_MODEL,
        "prompt": req.prompt,
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    if req.seed is not None:
        payload["seed"] = req.seed

    logger.info("SeedDream T2I: size=%s seed=%s prompt=%.80s", size, req.seed, req.prompt)
    url = _call_byteplus(payload)
    logger.info("SeedDream image generated: %s", url[:120])
    return ImageResponse(url=url, size=size, seed=req.seed)


# --- Image-to-Image (Seededit) ---

@router.post("/image/edit", response_model=ImageResponse)
async def edit_image(
    prompt: str = Form(...),
    size: str = Form("adaptive"),
    seed: Optional[int] = Form(None),
    image: UploadFile = File(...),
    _user=Depends(verify_api_key),
):
    """Edit an image using BytePlus SeedDream API (image-to-image)."""
    image_data = await image.read()
    if len(image_data) > 20 * 1024 * 1024:
        raise HTTPException(400, "Image file too large (max 20MB)")

    ext = (image.filename or "").rsplit(".", 1)[-1].lower()
    mime = "image/png" if ext == "png" else "image/jpeg"
    b64 = base64.b64encode(image_data).decode()
    image_url = f"data:{mime};base64,{b64}"

    size = _parse_size(size)
    payload = {
        "model": SEEDREAM_MODEL,
        "prompt": prompt,
        "image": image_url,
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    if seed is not None:
        payload["seed"] = seed

    logger.info("SeedDream I2I: size=%s seed=%s prompt=%.80s", size, seed, prompt)
    url = _call_byteplus(payload)
    logger.info("SeedDream edit generated: %s", url[:120])
    return ImageResponse(url=url, size=size, seed=seed)


# --- Multi-Reference Image Generation ---

MAX_REF_IMAGES = 10
MAX_REF_SIZE = 10 * 1024 * 1024  # 10MB per image


@router.post("/image/multiref", response_model=ImageResponse)
async def multiref_image(
    prompt: str = Form(...),
    size: str = Form("2048x2048"),
    seed: Optional[int] = Form(None),
    images: List[UploadFile] = File(...),
    _user=Depends(verify_api_key),
):
    """Generate image from multiple reference images using SeedDream (up to 10)."""
    if len(images) < 1:
        raise HTTPException(400, "At least 1 reference image is required")
    if len(images) > MAX_REF_IMAGES:
        raise HTTPException(400, f"Maximum {MAX_REF_IMAGES} reference images allowed")

    image_list = []
    for img in images:
        data = await img.read()
        if len(data) > MAX_REF_SIZE:
            raise HTTPException(400, f"Image '{img.filename}' too large (max 10MB each)")
        ext = (img.filename or "").rsplit(".", 1)[-1].lower()
        mime = "image/png" if ext == "png" else "image/jpeg"
        b64 = base64.b64encode(data).decode()
        image_list.append(f"data:{mime};base64,{b64}")

    size = _parse_size(size)
    payload = {
        "model": SEEDREAM_MODEL,
        "prompt": prompt,
        "image": image_list,
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    if seed is not None:
        payload["seed"] = seed

    logger.info("SeedDream MultiRef: %d images, size=%s seed=%s prompt=%.80s",
                len(image_list), size, seed, prompt)
    url = _call_byteplus(payload)
    logger.info("SeedDream multiref generated: %s", url[:120])
    return ImageResponse(url=url, size=size, seed=seed)
