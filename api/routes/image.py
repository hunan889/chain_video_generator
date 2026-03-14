"""SeedDream image generation via BytePlus API."""

import base64
import json
import logging
import math
import os
import requests as http_requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Query
from pydantic import BaseModel, Field
from typing import List, Optional
import pymysql

from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    _user=Depends(verify_api_key)
):
    """Upload an image file and return its URL."""
    from api.services import storage
    import uuid

    # Read file content
    content = await file.read()

    # Generate unique filename
    ext = file.filename.split('.')[-1] if '.' in file.filename else 'png'
    filename = f"{uuid.uuid4().hex}.{ext}"

    # Save to storage
    local_path, url = await storage.save_upload(content, filename)

    return {"url": url, "filename": filename}


BYTEPLUS_API_KEY = os.getenv(
    "BYTEPLUS_API_KEY", "f3cb7588-0af7-4753-96c4-8ca992600bca"
)
BYTEPLUS_ENDPOINT = "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations"
SEEDREAM_MODEL = os.getenv("SEEDREAM_MODEL", "seedream-5-0-260128")
MIN_PIXELS = 3686400  # ~1920x1920

SIZE_PRESETS = {
    "1K": "1024x1024",
    "2K": "2048x2048",
    "4K": "4096x4096",
}

# Database configuration
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}

def get_db():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)

def save_generation_history(generation_type: str, image_url: str, parameters: dict, user_id: str = 'default'):
    """保存图片生成历史"""
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO image_generation_history
            (user_id, generation_type, prompt, negative_prompt, model, size, width, height,
             seed, steps, cfg_scale, image_url, parameters)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            generation_type,
            parameters.get('prompt'),
            parameters.get('negative_prompt'),
            parameters.get('model'),
            parameters.get('size'),
            parameters.get('width'),
            parameters.get('height'),
            parameters.get('seed'),
            parameters.get('steps'),
            parameters.get('cfg_scale'),
            image_url,
            json.dumps(parameters)
        ))

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to save generation history: {e}")



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
        resp = http_requests.post(BYTEPLUS_ENDPOINT, json=payload, headers=headers, timeout=300)
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
    model: Optional[str] = Field(None, description="SeedDream model version (e.g. seedream-5-0-260128)")


class ImageResponse(BaseModel):
    url: str
    size: str
    seed: Optional[int] = None
    cropped_inputs: Optional[List[str]] = None


@router.post("/image/generate", response_model=ImageResponse)
async def generate_image(req: ImageRequest, _user=Depends(verify_api_key)):
    """Generate an image using BytePlus SeedDream API (text-to-image)."""
    size = _parse_size(req.size)
    model = req.model or SEEDREAM_MODEL
    payload = {
        "model": model,
        "prompt": req.prompt,
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    if req.seed is not None:
        payload["seed"] = req.seed

    logger.info("SeedDream T2I: model=%s size=%s seed=%s prompt=%.80s", model, size, req.seed, req.prompt)
    url = _call_byteplus(payload)
    logger.info("SeedDream image generated: %s", url[:120])

    # Save to history
    w, h = map(int, size.split('x'))
    save_generation_history('t2i', url, {
        'prompt': req.prompt,
        'model': model,
        'size': size,
        'width': w,
        'height': h,
        'seed': req.seed
    })

    return ImageResponse(url=url, size=size, seed=req.seed)


# --- Image-to-Image (Seededit) ---

@router.post("/image/edit", response_model=ImageResponse)
async def edit_image(
    prompt: str = Form(...),
    size: str = Form("adaptive"),
    seed: Optional[int] = Form(None),
    model: Optional[str] = Form(None),
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
    model_name = model or SEEDREAM_MODEL
    payload = {
        "model": model_name,
        "prompt": prompt,
        "image": image_url,
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    if seed is not None:
        payload["seed"] = seed

    logger.info("SeedDream I2I: model=%s size=%s seed=%s prompt=%.80s", model_name, size, seed, prompt)
    url = _call_byteplus(payload)
    logger.info("SeedDream edit generated: %s", url[:120])
    return ImageResponse(url=url, size=size, seed=seed)


def _crop_and_resize(image_data: bytes, target_w: int, target_h: int) -> bytes:
    """Top-center crop to target aspect ratio, then resize to exact target size."""
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(image_data))
    if img.mode == "RGBA":
        img = img.convert("RGB")
    iw, ih = img.size
    target_ratio = target_w / target_h
    img_ratio = iw / ih
    if abs(img_ratio - target_ratio) < 0.01 and iw == target_w and ih == target_h:
        return image_data  # Already matches
    # Top-center crop to target aspect ratio
    if img_ratio > target_ratio:
        # Image is wider — crop left/right equally, keep top
        new_w = int(ih * target_ratio)
        left = (iw - new_w) // 2
        img = img.crop((left, 0, left + new_w, ih))
    elif img_ratio < target_ratio:
        # Image is taller — crop bottom only, keep top
        new_h = int(iw / target_ratio)
        img = img.crop((0, 0, iw, new_h))
    # Resize to exact target size
    img = img.resize((target_w, target_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# --- Multi-Reference Image Generation ---

MAX_REF_IMAGES = 10
MAX_REF_SIZE = 10 * 1024 * 1024  # 10MB per image


@router.post("/image/multiref", response_model=ImageResponse)
async def multiref_image(
    prompt: str = Form(...),
    size: str = Form("2048x2048"),
    seed: Optional[int] = Form(None),
    model: Optional[str] = Form(None),
    images: List[UploadFile] = File(...),
    _user=Depends(verify_api_key),
):
    """Generate image from multiple reference images using SeedDream (up to 10)."""
    if len(images) < 1:
        raise HTTPException(400, "At least 1 reference image is required")
    if len(images) > MAX_REF_IMAGES:
        raise HTTPException(400, f"Maximum {MAX_REF_IMAGES} reference images allowed")

    size = _parse_size(size)
    target_w, target_h = map(int, size.lower().split("x"))

    image_list = []
    cropped_urls = []
    for img in images:
        data = await img.read()
        if len(data) > MAX_REF_SIZE:
            raise HTTPException(400, f"Image '{img.filename}' too large (max 10MB each)")
        # Crop to match output aspect ratio, then resize to output size
        data = _crop_and_resize(data, target_w, target_h)
        # Save cropped image for preview
        cropped_url = _save_result_image(base64.b64encode(data).decode())
        cropped_urls.append(cropped_url)
        b64 = base64.b64encode(data).decode()
        image_list.append(f"data:image/jpeg;base64,{b64}")
    model_name = model or SEEDREAM_MODEL
    payload = {
        "model": model_name,
        "prompt": prompt,
        "image": image_list,
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    if seed is not None:
        payload["seed"] = seed

    logger.info("SeedDream MultiRef: model=%s %d images, size=%s seed=%s prompt=%.80s",
                model_name, len(image_list), size, seed, prompt)
    url = _call_byteplus(payload)
    logger.info("SeedDream multiref generated: %s", url[:120])
    return ImageResponse(url=url, size=size, seed=seed, cropped_inputs=cropped_urls)


# --- Scene Character Swap (Reactor + SeedDream) ---

FORGE_URL = os.getenv("FORGE_URL", "http://127.0.0.1:7865")

SCENE_SWAP_DEFAULT_PROMPT = (
    "edit image 2, keep the position of image 2, and swap face to image 1, "
    "replace the character of image 2 to image 1, change face to image 1"
)


def _blend_expression(original_data: bytes, swapped_data: bytes, keep: float) -> bytes:
    """Blend original expression back into the Reactor-swapped face.

    Uses face detection to find the inner face region (eyes/nose/mouth),
    then alpha-blends original expression details back onto the swapped result.
    keep: 0.0 = full swap (no expression), 1.0 = full original expression.
    """
    from PIL import Image, ImageFilter, ImageDraw
    import io
    import numpy as np

    original = Image.open(io.BytesIO(original_data)).convert("RGB")
    swapped = Image.open(io.BytesIO(swapped_data)).convert("RGB")

    # Ensure same size
    if swapped.size != original.size:
        swapped = swapped.resize(original.size, Image.LANCZOS)

    w, h = original.size

    # Try to detect face with insightface for precise region
    face_box = None
    try:
        from insightface.app import FaceAnalysis
        app = FaceAnalysis(name="buffalo_l", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
        orig_np = np.array(original)
        faces = app.get(orig_np)
        if faces:
            box = faces[0].bbox.astype(int)
            face_box = (max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3]))
    except Exception as e:
        logger.warning("Face detection for expression blend failed, using center estimate: %s", e)

    if not face_box:
        # Fallback: assume face is roughly centered upper portion
        fw, fh = int(w * 0.4), int(h * 0.4)
        cx, cy = w // 2, int(h * 0.4)
        face_box = (cx - fw // 2, cy - fh // 2, cx + fw // 2, cy + fh // 2)

    # Create soft elliptical mask for inner face (eyes/nose/mouth area)
    # Shrink the detected box to inner region (70% of face box)
    bx1, by1, bx2, by2 = face_box
    bw, bh = int(bx2 - bx1), int(by2 - by1)
    shrink = 0.15
    inner_box = (
        int(bx1 + bw * shrink),
        int(by1 + bh * shrink),
        int(bx2 - bw * shrink),
        int(by2 - bh * shrink),
    )

    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(inner_box, fill=255)

    # Heavy gaussian blur for soft edges
    blur_radius = max(bw, bh) // 4
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(blur_radius, 10)))

    # Scale mask by keep factor
    mask_np = (np.array(mask, dtype=np.float32) / 255.0 * keep * 255).clip(0, 255).astype(np.uint8)
    mask = Image.fromarray(mask_np)

    # Blend: in masked region, mix original expression back
    result = Image.composite(original, swapped, mask)

    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _overlay_face_only(original_data: bytes, swapped_data: bytes) -> bytes:
    """Overlay only the core face skin from swapped onto original, preserving occlusions.

    Two-layer approach:
    1. Face polygon mask: limits swap to the face region (landmarks-based)
    2. Pixel-diff occlusion detection: within the face region, finds areas where
       Reactor over-modified (removed occlusions like hands, objects in mouth,
       glasses, etc.) and restores those from the original.
    """
    from PIL import Image, ImageFilter, ImageDraw
    import io
    import numpy as np

    original = Image.open(io.BytesIO(original_data)).convert("RGB")
    swapped = Image.open(io.BytesIO(swapped_data)).convert("RGB")

    if swapped.size != original.size:
        swapped = swapped.resize(original.size, Image.LANCZOS)

    w, h = original.size
    orig_np = np.array(original, dtype=np.float32)
    swap_np = np.array(swapped, dtype=np.float32)

    # --- Layer 1: Face polygon mask from landmarks ---
    landmarks = None
    face_box = None
    try:
        from insightface.app import FaceAnalysis
        app = FaceAnalysis(name="buffalo_l", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
        faces = app.get(np.array(original))
        if faces:
            face = faces[0]
            box = face.bbox.astype(int)
            face_box = (max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3]))
            if face.kps is not None:
                landmarks = face.kps.astype(int)
    except Exception as e:
        logger.warning("Face detection for occlusion mask failed: %s", e)

    if landmarks is not None and len(landmarks) >= 5:
        le, re, nose, lm, rm = landmarks[:5]
        eye_center = ((le[0] + re[0]) // 2, (le[1] + re[1]) // 2)
        eye_dist = int(np.linalg.norm(re - le))
        forehead = (eye_center[0], eye_center[1] - int(eye_dist * 0.6))
        mouth_center = ((lm[0] + rm[0]) // 2, (lm[1] + rm[1]) // 2)
        chin = (mouth_center[0], mouth_center[1] + int(eye_dist * 0.5))
        cheek_expand = int(eye_dist * 0.35)
        left_cheek = (min(le[0], lm[0]) - cheek_expand, (le[1] + lm[1]) // 2)
        right_cheek = (max(re[0], rm[0]) + cheek_expand, (re[1] + rm[1]) // 2)
        right_temple = (re[0] + cheek_expand, forehead[1])
        left_temple = (le[0] - cheek_expand, forehead[1])
        polygon = [
            forehead, right_temple, tuple(right_cheek), tuple(rm),
            chin, tuple(lm), tuple(left_cheek), left_temple,
        ]
        face_mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(face_mask).polygon(polygon, fill=255)
        blur_r = max(eye_dist // 3, 8)
        face_mask = face_mask.filter(ImageFilter.GaussianBlur(radius=blur_r))
    elif face_box:
        bx1, by1, bx2, by2 = face_box
        bw, bh = bx2 - bx1, by2 - by1
        shrink = 0.1
        inner = (int(bx1 + bw * shrink), int(by1 + bh * shrink),
                 int(bx2 - bw * shrink), int(by2 - bh * shrink))
        face_mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(face_mask).ellipse(inner, fill=255)
        face_mask = face_mask.filter(ImageFilter.GaussianBlur(radius=max(bw, bh) // 4))
    else:
        return swapped_data

    face_mask_np = np.array(face_mask, dtype=np.float32) / 255.0

    # --- Layer 2: Pixel-diff occlusion detection ---
    # Compare original vs swapped per-pixel; large diffs inside face = occlusion removed
    diff = np.abs(orig_np - swap_np).mean(axis=2)  # per-pixel mean channel diff

    # Percentile-based threshold: within the face region, ALL pixels have high diff
    # from the swap, but occlusion removal areas have the HIGHEST diff.
    # Use a percentile so the top N% of pixels get restored from original.
    face_pixels = diff[np.array(face_mask) > 128]
    if len(face_pixels) > 0:
        # 60th percentile: top ~40% of face-region diffs treated as occlusion
        occlusion_thresh = np.percentile(face_pixels, 60)
        # Ensure a minimum threshold to avoid false positives on low-diff faces
        occlusion_thresh = max(occlusion_thresh, 30)
        logger.info("Occlusion detection: median=%.1f, p60=%.1f, thresh=%.1f",
                     np.median(face_pixels), np.percentile(face_pixels, 60), occlusion_thresh)
    else:
        occlusion_thresh = 80

    # Occlusion mask: pixels within face region where diff is above threshold
    occlusion_detect = (diff > occlusion_thresh).astype(np.float32)
    # Only consider within face region
    occlusion_detect *= face_mask_np
    # Dilate aggressively + heavy blur to cover full occluded objects
    occ_img = Image.fromarray((occlusion_detect * 255).astype(np.uint8))
    occ_img = occ_img.filter(ImageFilter.MaxFilter(size=9))
    occ_img = occ_img.filter(ImageFilter.MaxFilter(size=7))
    occ_img = occ_img.filter(ImageFilter.GaussianBlur(radius=10))
    occlusion_np = np.array(occ_img, dtype=np.float32) / 255.0

    # --- Final composite ---
    # Start with original, overlay swapped face, then restore occlusions
    # Step A: face swap composite (swapped * face_mask + original * (1 - face_mask))
    face_alpha = face_mask_np[:, :, np.newaxis]
    composite = swap_np * face_alpha + orig_np * (1.0 - face_alpha)
    # Step B: restore occlusions from original
    occ_alpha = occlusion_np[:, :, np.newaxis]
    composite = orig_np * occ_alpha + composite * (1.0 - occ_alpha)

    result = Image.fromarray(composite.clip(0, 255).astype(np.uint8))
    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


@router.post("/image/scene-swap", response_model=ImageResponse)
async def scene_swap(
    face_image: UploadFile = File(...),
    scene_image: UploadFile = File(...),
    prompt: str = Form(SCENE_SWAP_DEFAULT_PROMPT),
    extra_prompt: str = Form(""),
    expression_keep: float = Form(0.0),
    preserve_occlusion: str = Form("false"),
    skip_reactor: str = Form("false"),
    reactor_only: str = Form("false"),
    size: str = Form("1024x1024"),
    seed: Optional[int] = Form(None),
    model: Optional[str] = Form(None),
    _user=Depends(verify_api_key),
):
    """Scene character replacement: swap the person in scene_image to face_image's identity.

    Step 1: Reactor face swap (scene face → target face)
    Step 2: SeedDream multiref edit with [face_image, swapped_scene]
    """
    preserve_occlusion = preserve_occlusion.lower() in ("true", "1", "yes")
    skip_reactor = skip_reactor.lower() in ("true", "1", "yes")
    reactor_only = reactor_only.lower() in ("true", "1", "yes")
    # preserve_occlusion is handled in Step 1.5 (_overlay_face_only)
    face_data = await face_image.read()
    scene_data = await scene_image.read()
    if len(face_data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Face image too large (max 10MB)")
    if len(scene_data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Scene image too large (max 10MB)")

    # Parse size: crop images at user-specified dimensions,
    # but let SeedDream output at upscaled size if needed
    size = _parse_size(size)  # may upscale for SeedDream min pixel requirement
    output_w, output_h = map(int, size.lower().split("x"))
    # Crop at user-requested resolution (cap to avoid huge Reactor input)
    crop_w = min(output_w, 1024)
    crop_h = min(output_h, 1024)
    # Preserve aspect ratio from output size
    if output_w != crop_w or output_h != crop_h:
        ratio = output_w / output_h
        if crop_w / crop_h > ratio:
            crop_w = int(crop_h * ratio)
        else:
            crop_h = int(crop_w / ratio)
    target_w, target_h = crop_w, crop_h

    # Step 0: Crop both images to target size first
    face_cropped = _crop_and_resize(face_data, target_w, target_h)
    scene_cropped = _crop_and_resize(scene_data, target_w, target_h)

    face_b64 = base64.b64encode(face_cropped).decode()

    if skip_reactor:
        # Skip Reactor — send original scene directly to SeedDream
        logger.info("SceneSwap: Reactor skipped, using original scene (%dx%d)", target_w, target_h)
        swapped_data = scene_cropped
    else:
        # Step 1: Reactor face swap on cropped scene
        scene_b64 = base64.b64encode(scene_cropped).decode()
        reactor_payload = {
            "source_image": face_b64,
            "target_image": scene_b64,
            "source_faces_index": [0],
            "face_index": [0],
            "model": "inswapper_128.onnx",
            "face_restorer": "CodeFormer",
            "restorer_visibility": 1,
            "codeformer_weight": 0.7,
            "restore_first": 1,
            "upscaler": "None",
            "scale": 1,
            "upscale_visibility": 1,
            "device": "CUDA",
            "mask_face": 1,
            "det_thresh": 0.5,
            "det_maxnum": 0,
        }

        logger.info("SceneSwap Step 1: Reactor face swap (%dx%d)...", target_w, target_h)
        try:
            reactor_resp = http_requests.post(
                f"{FORGE_URL}/reactor/image", json=reactor_payload, timeout=120
            )
        except http_requests.RequestException as e:
            raise HTTPException(502, f"Reactor request failed: {e}")

        if reactor_resp.status_code != 200:
            logger.error("Reactor error %d: %s", reactor_resp.status_code, reactor_resp.text[:500])
            raise HTTPException(502, f"Reactor face swap failed: {reactor_resp.text[:300]}")

        try:
            swapped_b64 = reactor_resp.json()["image"]
        except (KeyError, ValueError) as e:
            raise HTTPException(502, f"Unexpected Reactor response: {e}")

        swapped_data = base64.b64decode(swapped_b64)
        logger.info("SceneSwap Step 1 done: face swapped")

        # Step 1.5: Post-process swapped face
        if preserve_occlusion:
            logger.info("SceneSwap: preserving occlusions (face-only overlay)...")
            swapped_data = _overlay_face_only(scene_cropped, swapped_data)
            logger.info("SceneSwap: occlusion preservation done")
        elif expression_keep > 0:
            logger.info("SceneSwap blending expression (keep=%.2f)...", expression_keep)
            swapped_data = _blend_expression(scene_cropped, swapped_data, expression_keep)
            logger.info("SceneSwap expression blend done")

    # Save cropped previews
    swapped_b64_out = base64.b64encode(swapped_data).decode()
    cropped_urls = [
        _save_result_image(face_b64),
        _save_result_image(swapped_b64_out),
    ]

    # Reactor-only mode: skip SeedDream, return the swap result directly
    if reactor_only:
        url = _save_result_image(swapped_b64_out)
        logger.info("SceneSwap reactor-only completed: %s", url)
        return ImageResponse(url=url, size=f"{target_w}x{target_h}", seed=seed, cropped_inputs=cropped_urls)

    # Step 2: SeedDream multiref — [IMAGE 1: face, IMAGE 2: swapped scene]
    image_list = [
        f"data:image/jpeg;base64,{face_b64}",
        f"data:image/jpeg;base64,{swapped_b64_out}",
    ]

    full_prompt = prompt.strip()
    if preserve_occlusion:
        full_prompt += (", IMPORTANT: do not remove any objects from the face or mouth area, "
                        "keep all occlusions exactly as they appear in image 2, "
                        "preserve everything covering the face")
    if extra_prompt.strip():
        full_prompt = f"{full_prompt}, {extra_prompt.strip()}"

    model_name = model or SEEDREAM_MODEL
    payload = {
        "model": model_name,
        "prompt": full_prompt,
        "image": image_list,
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    if seed is not None:
        payload["seed"] = seed

    logger.info("SceneSwap Step 2: SeedDream multiref, prompt=%.120s", full_prompt)
    try:
        url = _call_byteplus(payload)
        logger.info("SceneSwap completed: %s", url[:120])
        return ImageResponse(url=url, size=size, seed=seed, cropped_inputs=cropped_urls)
    except HTTPException as e:
        # If SeedDream fails and we have a Reactor result, return it as fallback
        if skip_reactor:
            raise  # No Reactor result to fall back to, propagate the error
        logger.warning("SceneSwap SeedDream failed (%s), returning Reactor result as fallback", e.detail[:120] if e.detail else "")
        url = _save_result_image(swapped_b64_out)
        return ImageResponse(url=url, size=f"{target_w}x{target_h}", seed=seed, cropped_inputs=cropped_urls)


def _save_result_image(b64_data: str) -> str:
    """Save base64 image to uploads dir (served by /api/v1/results/) and return URL."""
    import uuid
    from api.config import UPLOADS_DIR
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    filepath = UPLOADS_DIR / filename
    with open(filepath, "wb") as f:
        f.write(base64.b64decode(b64_data))
    return f"/api/v1/results/{filename}"


INSTANTID_ADAPTER = "ip-adapter_instant_id_sdxl [eb2d3ec0]"
INSTANTID_CN_MODEL = "control_instant_id_sdxl [c5c25a50]"
IPADAPTER_FACEID_MODEL = "ip-adapter-faceid-plusv2_sdxl [187cb962]"
IPADAPTER_FACEID_MODULE = "InsightFace+CLIP-H (IPAdapter)"


def _build_couple_regions(n: int) -> list:
    """Build horizontal regions for N people: [[x1,x2,y1,y2,weight], ...]."""
    step = 1.0 / n
    return [[round(i * step, 4), round((i + 1) * step, 4), 0.0, 1.0, 1.0] for i in range(n)]


@router.post("/image/faceswap", response_model=ImageResponse)
async def faceswap_image(
    prompt: str = Form(...),
    negative_prompt: str = Form("deformed, blurry, bad anatomy, bad hands, extra fingers, ugly, cartoon, anime, painting, drawing"),
    width: int = Form(768),
    height: int = Form(1024),
    steps: int = Form(25),
    cfg_scale: float = Form(5.0),
    face_weight: float = Form(0.8),
    seed: int = Form(-1),
    faces: List[UploadFile] = File(...),
    _user=Depends(verify_api_key),
):
    """Generate image with face identity preservation.

    Single face: InstantID (face embedding + face keypoints).
    Multiple faces: Forge Couple (regional text) + ReActor (face swap per region).
    """
    if len(faces) < 1 or len(faces) > 5:
        raise HTTPException(400, "Provide 1-5 face images")

    # Read and encode face images
    face_b64_list = []
    face_raw_list = []
    for f in faces:
        data = await f.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(400, f"Face image '{f.filename}' too large (max 10MB)")
        face_raw_list.append(data)
        face_b64_list.append(base64.b64encode(data).decode())

    n = len(face_b64_list)

    # Normalize Windows line endings in prompt (textarea sends \r\n)
    prompt = prompt.replace("\r\n", "\n").replace("\r", "\n")

    # Auto-match output size to first face image aspect ratio
    if width <= 0 or height <= 0:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(face_raw_list[0]))
        iw, ih = img.size
        ratio = iw / ih
        # Target ~768*1024 total pixels, align to 64
        total = 768 * 1024
        height = int(math.sqrt(total / ratio))
        width = int(height * ratio)
        width = math.ceil(width / 64) * 64
        height = math.ceil(height / 64) * 64

    # Multi-face: ensure landscape and wide enough for N people side by side
    if n > 1:
        if height > width:
            width, height = height, width
        # Ensure at least 512px per person horizontally
        min_width = 512 * n
        if width < min_width:
            width = math.ceil(min_width / 64) * 64
        logger.info("Multi-face: landscape %dx%d for %d people", width, height, n)

    if n == 1:
        # Single face: InstantID (face embedding + keypoints)
        # Keypoints weight much lower to avoid face distortion
        kp_weight = face_weight * 0.6
        cn_units = [
            {
                "enabled": True,
                "image": face_b64_list[0],
                "module": "InsightFace (InstantID)",
                "model": INSTANTID_ADAPTER,
                "weight": face_weight,
                "resize_mode": 0,
                "control_mode": 0,
                "pixel_perfect": True,
            },
            {
                "enabled": True,
                "image": face_b64_list[0],
                "module": "instant_id_face_keypoints",
                "model": INSTANTID_CN_MODEL,
                "weight": kp_weight,
                "resize_mode": 0,
                "control_mode": 0,
                "pixel_perfect": True,
            },
        ]
        alwayson = {"controlnet": {"args": cn_units}}
    else:
        # Multiple faces: Forge Couple (regional text) + ReActor (face swap)
        # Step 1: Generate base image with regional text prompts
        alwayson = {
            "Forge Couple": {
                "args": [
                    True,                       # enable
                    "Basic",                    # region assignment
                    "\n",                       # couple separator
                    "Horizontal",               # tile direction
                    "None",                     # global effect
                    0.5,                        # global effect weight
                    _build_couple_regions(n),   # region definitions
                ]
            }
        }

        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "seed": seed,
            "sampler_name": "DPM++ 2M SDE",
            "scheduler": "Karras",
            "batch_size": 1,
            "resize_mode": 0,
            "alwayson_scripts": alwayson,
        }

        logger.info("Forge Couple+ReActor: %d faces, %dx%d, steps=%d, prompt=%r",
                     n, width, height, steps, prompt[:200])
        logger.info("Forge Couple regions: %s", _build_couple_regions(n))

        try:
            resp = http_requests.post(f"{FORGE_URL}/sdapi/v1/txt2img", json=payload, timeout=300)
        except http_requests.RequestException as e:
            raise HTTPException(502, f"Forge API request failed: {e}")

        if resp.status_code != 200:
            logger.error("Forge API error %d: %s", resp.status_code, resp.text[:500])
            raise HTTPException(resp.status_code, f"Forge API error: {resp.text[:500]}")

        try:
            img_b64 = resp.json()["images"][0]
        except (KeyError, IndexError, ValueError) as e:
            raise HTTPException(502, f"Unexpected Forge response: {e}")

        logger.info("Forge base image generated, swapping %d faces via ReActor...", n)

        # Step 2: Swap each face using ReActor
        for i, face_b64 in enumerate(face_b64_list):
            reactor_payload = {
                "source_image": face_b64,
                "target_image": img_b64,
                "source_faces_index": [0],
                "face_index": [i],
                "model": "inswapper_128.onnx",
                "face_restorer": "CodeFormer",
                "restorer_visibility": 1,
                "codeformer_weight": 0.7,
                "restore_first": 1,
                "upscaler": "None",
                "scale": 1,
                "upscale_visibility": 1,
                "device": "CUDA",
                "mask_face": 1,
                "det_thresh": 0.5,
                "det_maxnum": 0,
            }
            try:
                reactor_resp = http_requests.post(
                    f"{FORGE_URL}/reactor/image", json=reactor_payload, timeout=120
                )
                if reactor_resp.status_code == 200:
                    img_b64 = reactor_resp.json()["image"]
                    logger.info("ReActor swapped face %d/%d", i + 1, n)
                else:
                    logger.error("ReActor face %d failed %d: %s",
                                 i, reactor_resp.status_code, reactor_resp.text[:300])
            except http_requests.RequestException as e:
                logger.error("ReActor face %d request failed: %s", i, e)

        url = _save_result_image(img_b64)
        logger.info("Forge Couple+ReActor completed: %s", url)
        return ImageResponse(url=url, size=f"{width}x{height}", seed=seed)

    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg_scale": cfg_scale,
        "seed": seed,
        "sampler_name": "DPM++ 2M SDE",
        "scheduler": "Karras",
        "batch_size": 1,
        "resize_mode": 0,
        "alwayson_scripts": alwayson,
    }

    logger.info("Forge InstantID: 1 face, %dx%d, steps=%d, weight=%.1f, prompt=%r",
                width, height, steps, face_weight, prompt[:200])

    try:
        resp = http_requests.post(f"{FORGE_URL}/sdapi/v1/txt2img", json=payload, timeout=300)
    except http_requests.RequestException as e:
        raise HTTPException(502, f"Forge API request failed: {e}")

    if resp.status_code != 200:
        logger.error("Forge API error %d: %s", resp.status_code, resp.text[:500])
        raise HTTPException(resp.status_code, f"Forge API error: {resp.text[:500]}")

    try:
        img_b64 = resp.json()["images"][0]
        url = _save_result_image(img_b64)
    except (KeyError, IndexError, ValueError) as e:
        raise HTTPException(502, f"Unexpected Forge response: {e}")

    logger.info("Forge InstantID completed: %s", url)
    return ImageResponse(url=url, size=f"{width}x{height}", seed=seed)


# --- Pose Transfer (A's pose/scene + B's identity/appearance) ---

OPENPOSE_MODEL = "OpenPoseXL2 [f4251cb4]"
IPADAPTER_PLUS_MODEL = "ip-adapter-plus_sdxl_vit-h [f1f19f7d]"

# Structure ControlNet presets: (preprocessor_module, model)
STRUCTURE_PRESETS = {
    "canny": ("canny", "diffusers_xl_canny_full [2b69fca4]"),
    "depth": ("depth_anything_v2", "diffusers_xl_depth_full [2f51180b]"),
    "softedge": ("softedge_teed", "mistoLine_softedge_sdxl_fp16 [2583d73c]"),
}


@router.post("/image/transfer", response_model=ImageResponse)
async def transfer_image(
    prompt: str = Form("a person, realistic photo, high quality, natural lighting"),
    negative_prompt: str = Form("deformed, blurry, bad anatomy, bad hands, extra fingers, ugly, cartoon, anime, painting, drawing, overexposed, oversaturated, plastic skin, airbrushed, glossy skin, shiny skin, doll"),
    steps: int = Form(30),
    cfg_scale: float = Form(3.0),
    structure_mode: str = Form("none"),
    structure_weight: float = Form(0.85),
    pose_weight: float = Form(0.8),
    face_weight: float = Form(0.8),
    appearance_weight: float = Form(0.6),
    enable_face: bool = Form(True),
    enable_appearance: bool = Form(True),
    width: int = Form(0),
    height: int = Form(0),
    seed: int = Form(-1),
    pose_image: Optional[UploadFile] = File(None),
    ref_image: UploadFile = File(...),
    _user=Depends(verify_api_key),
):
    """Transfer pose from image A with identity/appearance from image B.

    Uses txt2img with ControlNet OpenPose (pose from A) + InstantID (face from B)
    + IP-Adapter (appearance from B). Does NOT repaint A — generates a fresh image.
    """
    if not prompt or not prompt.strip():
        prompt = "a person, raw photo, natural skin texture, pores, natural lighting, shot on Canon EOS R5, 85mm f/1.4"

    from PIL import Image as PILImage
    import io

    # Read pose image (optional)
    has_pose = pose_image is not None and pose_image.filename
    pose_b64 = None
    if has_pose:
        pose_data = await pose_image.read()
        if len(pose_data) > 20 * 1024 * 1024:
            raise HTTPException(400, "Pose image too large (max 20MB)")
        if len(pose_data) > 0:
            pose_b64 = base64.b64encode(pose_data).decode()
        else:
            has_pose = False

    ref_data = await ref_image.read()
    if len(ref_data) > 20 * 1024 * 1024:
        raise HTTPException(400, "Reference image too large (max 20MB)")
    ref_b64 = base64.b64encode(ref_data).decode()

    # Detect dimensions from pose image or ref image, preserve aspect ratio
    if width <= 0 or height <= 0:
        size_src = PILImage.open(io.BytesIO(pose_data if has_pose else ref_data))
        width, height = size_src.size
    aspect = width / max(height, 1)
    # Scale up to SDXL sweet spot (~1024 on short side)
    short_side = min(width, height)
    if short_side < 1024:
        scale = 1024 / short_side
        width = int(width * scale)
        height = int(height * scale)
    # Scale down if too large
    long_side = max(width, height)
    if long_side > 1536:
        scale = 1536 / long_side
        width = int(width * scale)
        height = int(height * scale)
    # Align to 64
    width = math.ceil(width / 64) * 64
    height = math.ceil(height / 64) * 64

    # Build ControlNet units
    cn_units = []

    # OpenPose + Structure only when pose image is provided
    if has_pose:
        cn_units.append({
            "enabled": True,
            "image": pose_b64,
            "module": "dw_openpose_full",
            "model": OPENPOSE_MODEL,
            "weight": pose_weight,
            "resize_mode": 0,
            "control_mode": 0,
            "pixel_perfect": True,
        })
        # Structure preservation (Canny/Depth/SoftEdge)
        if structure_mode and structure_mode != "none" and structure_mode in STRUCTURE_PRESETS:
            s_module, s_model = STRUCTURE_PRESETS[structure_mode]
            cn_units.append({
                "enabled": True,
                "image": pose_b64,
                "module": s_module,
                "model": s_model,
                "weight": structure_weight,
                "resize_mode": 0,
                "control_mode": 0,
                "pixel_perfect": True,
            })

    if enable_face:
        kp_weight = face_weight * 0.6
        cn_units.append({
            "enabled": True,
            "image": ref_b64,
            "module": "InsightFace (InstantID)",
            "model": INSTANTID_ADAPTER,
            "weight": face_weight,
            "resize_mode": 0,
            "control_mode": 0,
            "pixel_perfect": True,
        })
        cn_units.append({
            "enabled": True,
            "image": ref_b64,
            "module": "instant_id_face_keypoints",
            "model": INSTANTID_CN_MODEL,
            "weight": kp_weight,
            "resize_mode": 0,
            "control_mode": 0,
            "pixel_perfect": True,
        })

    if enable_appearance:
        cn_units.append({
            "enabled": True,
            "image": ref_b64,
            "module": "CLIP-ViT-H (IPAdapter)",
            "model": IPADAPTER_PLUS_MODEL,
            "weight": appearance_weight,
            "resize_mode": 0,
            "control_mode": 0,
            "pixel_perfect": True,
        })

    alwayson = {"controlnet": {"args": cn_units}}

    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg_scale": cfg_scale,
        "seed": seed,
        "sampler_name": "DPM++ 2M SDE",
        "scheduler": "Karras",
        "batch_size": 1,
        "resize_mode": 0,
        "alwayson_scripts": alwayson,
    }

    features = []
    if has_pose:
        features.append(f"Pose(w={pose_weight})")
        if structure_mode != "none":
            features.append(f"{structure_mode}(w={structure_weight})")
    if enable_face:
        features.append(f"InstantID(w={face_weight})")
    if enable_appearance:
        features.append(f"IPAdapter(w={appearance_weight})")
    logger.info("Transfer txt2img: %dx%d, %s, prompt=%r",
                width, height, "+".join(features), prompt[:200])

    try:
        resp = http_requests.post(f"{FORGE_URL}/sdapi/v1/txt2img", json=payload, timeout=300)
    except http_requests.RequestException as e:
        raise HTTPException(502, f"Forge API request failed: {e}")

    if resp.status_code != 200:
        logger.error("Forge API error %d: %s", resp.status_code, resp.text[:500])
        raise HTTPException(resp.status_code, f"Forge API error: {resp.text[:500]}")

    try:
        img_b64 = resp.json()["images"][0]
    except (KeyError, IndexError, ValueError) as e:
        raise HTTPException(502, f"Unexpected Forge response: {e}")

    # ReActor face refinement: swap reference face onto result for precise identity
    if enable_face:
        logger.info("Transfer: refining face via ReActor...")
        reactor_payload = {
            "source_image": ref_b64,
            "target_image": img_b64,
            "source_faces_index": [0],
            "face_index": [0],
            "model": "inswapper_128.onnx",
            "face_restorer": "CodeFormer",
            "restorer_visibility": 1,
            "codeformer_weight": 0.7,
            "restore_first": 1,
            "upscaler": "None",
            "scale": 1,
            "upscale_visibility": 1,
            "device": "CUDA",
            "mask_face": 1,
            "det_thresh": 0.5,
            "det_maxnum": 0,
        }
        try:
            reactor_resp = http_requests.post(
                f"{FORGE_URL}/reactor/image", json=reactor_payload, timeout=120
            )
            if reactor_resp.status_code == 200:
                img_b64 = reactor_resp.json()["image"]
                logger.info("Transfer: ReActor face refinement done")
            else:
                logger.warning("ReActor refinement failed %d, using original",
                               reactor_resp.status_code)
        except http_requests.RequestException as e:
            logger.warning("ReActor refinement request failed: %s, using original", e)

    url = _save_result_image(img_b64)
    logger.info("Transfer completed: %s", url)
    return ImageResponse(url=url, size=f"{width}x{height}", seed=seed)


# --- Z-Image I2I (Uncensored Image Editing via ComfyUI) ---

COMFYUI_URL = os.getenv("COMFYUI_A14B_URL", "http://127.0.0.1:8188")


def _upload_image_to_comfyui(image_data: bytes, filename: str) -> str:
    """Upload an image to ComfyUI and return the server-side filename."""
    resp = http_requests.post(
        f"{COMFYUI_URL}/upload/image",
        files={"image": (filename, image_data, "image/png")},
        data={"overwrite": "true"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(502, f"ComfyUI upload failed: {resp.text[:300]}")
    return resp.json()["name"]


def _queue_comfyui_prompt(workflow: dict) -> str:
    """Queue a workflow prompt on ComfyUI and return the prompt_id."""
    resp = http_requests.post(
        f"{COMFYUI_URL}/prompt",
        json={"prompt": workflow},
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(502, f"ComfyUI prompt queue failed: {resp.text[:300]}")
    return resp.json()["prompt_id"]


def _poll_comfyui_result(prompt_id: str, timeout: int = 120) -> str:
    """Poll ComfyUI for completion and return the output image filename."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = http_requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if prompt_id in data:
                outputs = data[prompt_id].get("outputs", {})
                for node_id, node_out in outputs.items():
                    images = node_out.get("images", [])
                    if images:
                        return images[0]["filename"]
                status = data[prompt_id].get("status", {})
                if status.get("status_str") == "error":
                    raise HTTPException(502, "ComfyUI workflow execution failed")
        time.sleep(1.5)
    raise HTTPException(504, "ComfyUI workflow timed out")


def _get_comfyui_image_b64(filename: str, subfolder: str = "") -> str:
    """Fetch a result image from ComfyUI and return as base64."""
    params = {"filename": filename}
    if subfolder:
        params["subfolder"] = subfolder
    resp = http_requests.get(f"{COMFYUI_URL}/view", params=params, timeout=30)
    if resp.status_code != 200:
        raise HTTPException(502, f"ComfyUI image fetch failed: {resp.status_code}")
    return base64.b64encode(resp.content).decode()


@router.post("/image/zimage-edit", response_model=ImageResponse)
async def zimage_edit(
    prompt: str = Form(...),
    negative_prompt: str = Form(""),
    denoise: float = Form(0.75),
    steps: int = Form(8),
    cfg: float = Form(1.0),
    seed: int = Form(-1),
    controlnet_strength: float = Form(0.0),
    image: UploadFile = File(...),
    _user=Depends(verify_api_key),
):
    """Edit an image using Z-Image-Turbo (uncensored I2I via local ComfyUI).

    denoise: 0.0 = no change, 1.0 = full regeneration. Recommended: 0.4-0.7
    controlnet_strength: 0.0 = disabled, 0.5-1.0 = preserve structure via Canny ControlNet
    """
    image_data = await image.read()
    if len(image_data) > 20 * 1024 * 1024:
        raise HTTPException(400, "Image file too large (max 20MB)")

    # Upload image to ComfyUI
    ext = (image.filename or "upload.png").rsplit(".", 1)[-1].lower()
    upload_name = _upload_image_to_comfyui(image_data, f"zimage_input.{ext}")

    # Build workflow
    import json, random
    workflow_path = os.path.join(os.path.dirname(__file__), "../../workflows/z_image_i2i.json")
    with open(workflow_path) as f:
        workflow = json.load(f)

    actual_seed = seed if seed >= 0 else random.randint(0, 2**63)
    workflow["4"]["inputs"]["text"] = prompt
    workflow["5"]["inputs"]["image"] = upload_name
    workflow["7"]["inputs"]["seed"] = actual_seed
    workflow["7"]["inputs"]["steps"] = steps
    workflow["7"]["inputs"]["cfg"] = cfg
    workflow["7"]["inputs"]["denoise"] = denoise
    workflow["9"]["inputs"]["text"] = negative_prompt

    # Inject ControlNet nodes when enabled (Z-Image uses ZImageFunControlnet)
    use_cn = controlnet_strength > 0.0
    if use_cn:
        # Node 11: Canny edge detection on input image
        workflow["11"] = {
            "class_type": "Canny",
            "inputs": {
                "image": ["5", 0],
                "low_threshold": 0.4,
                "high_threshold": 0.8,
            },
        }
        # Node 12: Load model patch
        workflow["12"] = {
            "class_type": "ModelPatchLoader",
            "inputs": {
                "name": "Z-Image-Turbo-Fun-Controlnet-Union.safetensors",
            },
        }
        # Node 13: Apply Z-Image ControlNet (patches model directly)
        workflow["13"] = {
            "class_type": "ZImageFunControlnet",
            "inputs": {
                "model": ["1", 0],
                "model_patch": ["12", 0],
                "vae": ["3", 0],
                "strength": controlnet_strength,
                "image": ["11", 0],
            },
        }
        # Rewire KSampler to use patched model
        workflow["7"]["inputs"]["model"] = ["13", 0]

    logger.info("Z-Image I2I: denoise=%.2f steps=%d seed=%d cn=%.2f prompt=%.80s",
                denoise, steps, actual_seed, controlnet_strength, prompt)

    # Queue and wait
    prompt_id = _queue_comfyui_prompt(workflow)
    result_filename = _poll_comfyui_result(prompt_id)
    img_b64 = _get_comfyui_image_b64(result_filename)
    url = _save_result_image(img_b64)

    logger.info("Z-Image I2I completed: %s", url)

    # Save to history
    save_generation_history('zimage-edit', url, {
        'prompt': prompt,
        'negative_prompt': negative_prompt,
        'denoise': denoise,
        'steps': steps,
        'cfg_scale': cfg,
        'seed': actual_seed,
        'controlnet_strength': controlnet_strength
    })

    return ImageResponse(url=url, size="auto", seed=actual_seed)


@router.post("/image/character-consistency", response_model=ImageResponse)
async def character_consistency(
    prompt: str = Form(...),
    negative_prompt: str = Form("deformed, blurry, bad anatomy, bad hands, extra fingers, ugly, cartoon, anime, painting, drawing, low quality"),
    width: int = Form(768),
    height: int = Form(1024),
    steps: int = Form(30),
    cfg: float = Form(5.0),
    seed: int = Form(-1),
    instantid_weight: float = Form(0.8),
    faceid_weight: float = Form(0.7),
    ipadapter_weight: float = Form(0.5),
    face_image: UploadFile = File(...),
    _user=Depends(verify_api_key),
):
    """Generate image with enhanced character consistency using InstantID + FaceID + IP-Adapter.

    This combines three powerful techniques:
    - InstantID: Face embedding + face keypoints for structure
    - FaceID: Enhanced face identity preservation
    - IP-Adapter: Overall style and appearance consistency
    """
    # Read face image
    face_data = await face_image.read()
    if len(face_data) > 20 * 1024 * 1024:
        raise HTTPException(400, "Face image too large (max 20MB)")

    # Upload image to ComfyUI
    ext = (face_image.filename or "face.png").rsplit(".", 1)[-1].lower()
    upload_name = _upload_image_to_comfyui(face_data, f"character_face.{ext}")

    # Load workflow
    import json, random
    workflow_path = os.path.join(os.path.dirname(__file__), "../../workflows/character_consistency.json")
    with open(workflow_path) as f:
        workflow = json.load(f)

    # Set parameters
    actual_seed = seed if seed >= 0 else random.randint(0, 2**63)

    workflow["2"]["inputs"]["text"] = prompt
    workflow["3"]["inputs"]["text"] = negative_prompt
    workflow["4"]["inputs"]["width"] = width
    workflow["4"]["inputs"]["height"] = height
    workflow["5"]["inputs"]["image"] = upload_name
    workflow["8"]["inputs"]["weight"] = instantid_weight
    workflow["11"]["inputs"]["weight"] = faceid_weight
    workflow["11"]["inputs"]["weight_faceidv2"] = faceid_weight
    workflow["14"]["inputs"]["weight"] = ipadapter_weight
    workflow["15"]["inputs"]["seed"] = actual_seed
    workflow["15"]["inputs"]["steps"] = steps
    workflow["15"]["inputs"]["cfg"] = cfg

    logger.info("Character Consistency: instantid=%.2f faceid=%.2f ipadapter=%.2f steps=%d seed=%d prompt=%.80s",
                instantid_weight, faceid_weight, ipadapter_weight, steps, actual_seed, prompt)

    # Queue and wait
    prompt_id = _queue_comfyui_prompt(workflow)
    result_filename = _poll_comfyui_result(prompt_id)
    img_b64 = _get_comfyui_image_b64(result_filename)
    url = _save_result_image(img_b64)

    logger.info("Character Consistency completed: %s", url)

    # Save to history
    save_generation_history('character-consistency', url, {
        'prompt': prompt,
        'negative_prompt': negative_prompt,
        'width': width,
        'height': height,
        'steps': steps,
        'cfg_scale': cfg,
        'seed': actual_seed,
        'instantid_weight': instantid_weight,
        'faceid_weight': faceid_weight,
        'ipadapter_weight': ipadapter_weight
    })

    return ImageResponse(url=url, size=f"{width}x{height}", seed=actual_seed)


class HistoryItem(BaseModel):
    id: int
    generation_type: str
    prompt: Optional[str]
    negative_prompt: Optional[str]
    model: Optional[str]
    size: Optional[str]
    width: Optional[int]
    height: Optional[int]
    seed: Optional[int]
    steps: Optional[int]
    cfg_scale: Optional[float]
    image_url: str
    created_at: str


class HistoryResponse(BaseModel):
    items: List[HistoryItem]
    total: int
    page: int
    page_size: int
    total_pages: int


@router.get("/image/history", response_model=HistoryResponse)
async def get_image_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    generation_type: Optional[str] = Query(None),
    _user=Depends(verify_api_key)
):
    """获取图片生成历史"""
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        where_clauses = []
        params = []

        if generation_type:
            where_clauses.append("generation_type = %s")
            params.append(generation_type)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # 获取总数
        cursor.execute(f"SELECT COUNT(*) as total FROM image_generation_history WHERE {where_sql}", params)
        total = cursor.fetchone()['total']

        # 获取历史记录
        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT id, generation_type, prompt, negative_prompt, model, size, width, height,
                   seed, steps, cfg_scale, image_url, created_at
            FROM image_generation_history
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])

        items = cursor.fetchall()

        return {
            'items': items,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        }

    finally:
        conn.close()


@router.post("/video/faceswap")
async def faceswap_video(
    video: UploadFile = File(...),
    face_image: UploadFile = File(...),
    faces_index: str = Form("0"),
    _user=Depends(verify_api_key),
):
    """Swap faces in a video using a reference face image.

    Args:
        video: Input video file
        face_image: Reference face image to swap into the video
        faces_index: Comma-separated indices of faces to swap (default: "0" for first face)
    """
    from api.main import task_manager
    import json

    # Read video
    video_data = await video.read()
    if len(video_data) > 500 * 1024 * 1024:  # 500MB limit
        raise HTTPException(400, "Video too large (max 500MB)")

    # Read face image
    face_data = await face_image.read()
    if len(face_data) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(400, "Face image too large (max 10MB)")

    # Save uploads
    from api.services import storage
    video_filename, _ = await storage.save_upload(video_data, video.filename or "input.mp4")
    face_filename, _ = await storage.save_upload(face_data, face_image.filename or "face.png")

    # Upload to ComfyUI
    client = task_manager.clients.get("a14b")
    if not client or not await client.is_alive():
        raise HTTPException(503, "ComfyUI service unavailable")

    video_upload = await client.upload_video(video_data, video_filename)
    face_upload = await client.upload_image(face_data, face_filename)

    # Load workflow
    from api.services.workflow_builder import load_workflow
    workflow = load_workflow("video_faceswap")

    # Set parameters
    workflow["1"]["inputs"]["video"] = video_upload.get("name", video_filename)
    workflow["4"]["inputs"]["image"] = face_upload.get("name", face_filename)
    workflow["5"]["inputs"]["faces_index"] = faces_index

    # Submit to ComfyUI
    prompt_id = await client.queue_prompt(workflow)

    # Create task
    task_id = await task_manager.create_task(
        "video_faceswap",
        {"video": video_filename, "face": face_filename, "faces_index": faces_index},
        prompt_id,
        "a14b"
    )

    return {"task_id": task_id, "status": "queued"}
