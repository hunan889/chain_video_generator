"""Stage 3 -- SeeDream Editing.

Optionally edits the first frame using BytePlus SeeDream API to transfer
identity features (face, accessories, clothing) from a reference image
onto the scene image.

This stage is:
  - Required for ``full_body_reference`` mode
  - Optional (default on) for ``face_reference`` mode
  - Skipped for ``first_frame`` and ``t2v`` modes
  - Skipped for continuations
"""

import asyncio
import base64
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from api_gateway.config import GatewayConfig
from api_gateway.services.external.byteplus import BytePlusClient
from api_gateway.services.gpu_clients.faceswap import ReactorClient
from shared.cos.client import COSClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeeDreamResult:
    """Immutable result of Stage 3 SeeDream editing."""

    url: Optional[str]  # edited frame URL, or None if skipped
    skipped: bool
    skip_reason: str = ""
    face_swapped: bool = False
    model: Optional[str] = None
    api_status: Optional[str] = None  # "success" / "failed"
    fallback_used: bool = False
    fallback_reason: str = ""
    error: Optional[str] = None
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _download_image_as_b64(url: str) -> str:
    """Download image from URL and return as raw base64 string."""
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to download image ({resp.status}): {url}")
            data = await resp.read()
    return base64.b64encode(data).decode()


async def _download_image_bytes(url: str) -> bytes:
    """Download image from URL and return raw bytes."""
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to download image ({resp.status}): {url}")
            return await resp.read()


def _upload_bytes_to_cos(
    data: bytes, cos_client: COSClient, subdir: str, filename: str
) -> str:
    """Write bytes to a temp file, upload to COS, return URL."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return cos_client.upload_file(tmp_path, subdir, filename)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def build_seedream_prompt(
    swap_face: bool = True,
    swap_accessories: bool = False,
    swap_expression: bool = False,
    swap_clothing: bool = False,
) -> str:
    """Build SeeDream prompt dynamically based on toggle switches.

    This is a pure function -- no external dependencies.
    """
    parts = ["edit image 2, keep the position and pose of image 2"]

    if swap_face:
        parts.append(
            "swap face to image 1, change face identity to match image 1, "
            "change hairstyle to match image 1"
        )
    else:
        parts.append("keep face identity the same as image 2")

    if swap_accessories:
        parts.append(
            "change accessories (jewelry, glasses, hair accessories) to match image 1"
        )
    else:
        parts.append("keep accessories the same as image 2")

    if swap_expression:
        parts.append("change facial expression to match image 1")
    else:
        parts.append("preserve the facial expression from image 2")

    if swap_clothing:
        parts.append("change clothing to match image 1")
    else:
        parts.append("keep clothing the same as image 2")

    parts.append("keep background the same as image 2")
    return ", ".join(parts)


def _resolve_seedream_prompt(
    seedream_config: dict,
    user_prompt: str,
) -> str:
    """Resolve the final SeeDream prompt from config + user prompt."""
    custom_prompt = seedream_config.get("prompt")
    swap_face = seedream_config.get("swap_face")
    swap_accessories = seedream_config.get("swap_accessories", False)
    swap_expression = seedream_config.get("swap_expression", False)
    swap_clothing = seedream_config.get("swap_clothing", False)

    if custom_prompt:
        prompt = custom_prompt
    elif swap_face is not None:
        # New toggle-based mode
        prompt = build_seedream_prompt(
            swap_face=swap_face,
            swap_accessories=swap_accessories,
            swap_expression=swap_expression,
            swap_clothing=swap_clothing,
        )
    else:
        # Legacy mode-based
        edit_mode = seedream_config.get("mode", "face_wearings")
        prompt = _resolve_legacy_mode_prompt(edit_mode)

    if user_prompt:
        prompt = f"{prompt}. {user_prompt}"
    return prompt


def _resolve_legacy_mode_prompt(mode: str) -> str:
    """Map legacy mode string to toggle-based prompt."""
    mode_toggles = {
        "face_only": (True, False, False, False),
        "face_wearings": (True, True, False, False),
        "full_body": (True, True, False, True),
    }
    toggles = mode_toggles.get(mode, (True, True, False, False))
    return build_seedream_prompt(*toggles)


def _compute_seedream_size(
    resolution: str,
    aspect_ratio: Optional[str],
) -> str:
    """Compute SeeDream output size from resolution + aspect ratio.

    Returns "WIDTHxHEIGHT" string.
    """
    import re

    # Parse aspect ratio
    if aspect_ratio:
        ar_parts = aspect_ratio.split(":")
        ar_w, ar_h = int(ar_parts[0]), int(ar_parts[1])
    else:
        # Derive from resolution string
        if "16_9" in resolution or "16:9" in resolution:
            ar_w, ar_h = 16, 9
        elif "3_4" in resolution or "3:4" in resolution:
            ar_w, ar_h = 3, 4
        else:
            ar_w, ar_h = 3, 4

    # Extract p-value from resolution string, minimum 720p for SeeDream quality
    res_match = re.match(r"(\d+)", resolution)
    p_val = int(res_match.group(1)) if res_match else 480
    p_val = max(p_val, 720)

    # Calculate dimensions, rounded to nearest 8
    if ar_w >= ar_h:
        height = round(p_val / 8) * 8
        width = round(p_val * ar_w / ar_h / 8) * 8
    else:
        width = round(p_val / 8) * 8
        height = round(p_val * ar_h / ar_w / 8) * 8

    # BytePlus SeeDream requires output size >= 3,686,400 pixels (e.g. 2560x1440).
    # Scale up proportionally if the computed size is too small.
    _MIN_PIXELS = 3_686_400
    if width * height < _MIN_PIXELS:
        import math
        scale = math.sqrt(_MIN_PIXELS / (width * height))
        width = math.ceil(width * scale / 8) * 8
        height = math.ceil(height * scale / 8) * 8

    return f"{width}x{height}"


async def _apply_reactor_fallback(
    target_url: str,
    reference_url: str,
    strength: float,
    reactor_client: ReactorClient,
    cos_client: COSClient,
    workflow_id: str,
) -> Optional[str]:
    """Apply Reactor face swap as a fallback.  Returns COS URL or None."""
    try:
        target_b64, ref_b64 = await asyncio.gather(
            _download_image_as_b64(target_url),
            _download_image_as_b64(reference_url),
        )
        swapped_bytes = await reactor_client.swap_face(
            source_image_b64=ref_b64,
            target_image_b64=target_b64,
            strength=strength,
        )
        filename = f"reactor_fallback_{uuid.uuid4().hex[:8]}.png"
        return _upload_bytes_to_cos(swapped_bytes, cos_client, "frames", filename)
    except Exception as exc:
        logger.warning("[%s] Reactor fallback failed: %s", workflow_id, exc)
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def edit_first_frame(
    workflow_id: str,
    first_frame_url: str,
    reference_image_url: Optional[str],
    mode: str,
    seedream_config: dict,
    face_swap_config: dict,
    user_prompt: str,
    reactor_deferred: bool,
    is_continuation: bool,
    resolution: str,
    aspect_ratio: Optional[str],
    config: GatewayConfig,
    byteplus_client: Optional[BytePlusClient],
    reactor_client: Optional[ReactorClient],
    cos_client: COSClient,
) -> SeeDreamResult:
    """Edit the first frame using SeeDream (or apply deferred Reactor).

    Returns a ``SeeDreamResult`` with the edited frame URL.
    """
    # ------------------------------------------------------------------
    # Quick-skip conditions
    # ------------------------------------------------------------------
    if is_continuation:
        return SeeDreamResult(
            url=None,
            skipped=True,
            skip_reason="continuation mode, skipping SeeDream",
        )

    if mode in ("first_frame", "t2v"):
        # Check if deferred reactor should be applied even though SeeDream is skipped
        if reactor_deferred and reference_image_url and reactor_client:
            swapped = await _apply_reactor_fallback(
                target_url=first_frame_url,
                reference_url=reference_image_url,
                strength=face_swap_config.get("strength", 1.0),
                reactor_client=reactor_client,
                cos_client=cos_client,
                workflow_id=workflow_id,
            )
            if swapped:
                return SeeDreamResult(
                    url=swapped,
                    skipped=True,
                    skip_reason="first_frame/T2V mode, applied deferred Reactor only",
                    face_swapped=True,
                )
        return SeeDreamResult(
            url=None,
            skipped=True,
            skip_reason="first_frame/T2V mode, skipping SeeDream",
        )

    # ------------------------------------------------------------------
    # Determine if SeeDream should run
    # ------------------------------------------------------------------
    should_run = False
    skip_reason = ""

    if mode == "full_body_reference":
        should_run = True
    elif mode == "face_reference":
        should_run = seedream_config.get("enabled", True)
        if not should_run:
            skip_reason = "SeeDream disabled in config"
    else:
        skip_reason = f"unsupported mode: {mode}"

    if not should_run:
        # If deferred reactor, still apply it
        if reactor_deferred and reference_image_url and reactor_client:
            swapped = await _apply_reactor_fallback(
                target_url=first_frame_url,
                reference_url=reference_image_url,
                strength=face_swap_config.get("strength", 1.0),
                reactor_client=reactor_client,
                cos_client=cos_client,
                workflow_id=workflow_id,
            )
            if swapped:
                return SeeDreamResult(
                    url=swapped,
                    skipped=True,
                    skip_reason=skip_reason,
                    face_swapped=True,
                )
        return SeeDreamResult(url=None, skipped=True, skip_reason=skip_reason)

    # ------------------------------------------------------------------
    # SeeDream execution
    # ------------------------------------------------------------------
    if not reference_image_url:
        return SeeDreamResult(
            url=None,
            skipped=True,
            skip_reason="no reference image for SeeDream",
        )

    if not byteplus_client:
        return SeeDreamResult(
            url=None,
            skipped=True,
            skip_reason="BytePlus client not configured",
            error="BytePlus client unavailable",
        )

    # Compute size
    size = seedream_config.get("size")
    if not size:
        try:
            size = _compute_seedream_size(resolution, aspect_ratio)
        except Exception as exc:
            logger.warning("[%s] Failed to compute SeeDream size: %s", workflow_id, exc)
            size = "1664x2216"  # ~3.69M px, meets BytePlus minimum

    # Resolve prompt
    prompt = _resolve_seedream_prompt(seedream_config, user_prompt)
    seed = seedream_config.get("seed")

    logger.info(
        "[%s] SeeDream: size=%s, prompt=%s...", workflow_id, size, prompt[:100]
    )

    try:
        # Download images and encode as base64 data URIs for BytePlus API
        scene_b64, ref_b64 = await asyncio.gather(
            _download_image_as_b64(first_frame_url),
            _download_image_as_b64(reference_image_url),
        )

        # BytePlus API expects images as list of data-URI dicts
        image_list = [
            f"data:image/jpeg;base64,{ref_b64}",    # Image 1: Reference
            f"data:image/jpeg;base64,{scene_b64}",   # Image 2: Scene
        ]

        result_url = await byteplus_client.generate_image(
            prompt=prompt,
            images=image_list,
            size=size,
            seed=seed,
        )

        # Download from BytePlus and re-upload to COS for consistent storage
        result_bytes = await _download_image_bytes(result_url)
        filename = f"seedream_{uuid.uuid4().hex[:8]}.png"
        cos_url = _upload_bytes_to_cos(result_bytes, cos_client, "frames", filename)

        logger.info("[%s] SeeDream succeeded: %s", workflow_id, cos_url)

        return SeeDreamResult(
            url=cos_url,
            skipped=False,
            face_swapped=False,  # SeeDream handles identity transfer, not Reactor
            model=byteplus_client.model,
            api_status="success",
        )

    except Exception as exc:
        logger.error("[%s] SeeDream failed: %s", workflow_id, exc, exc_info=True)

        # For full_body_reference, SeeDream is required -- propagate error
        if mode == "full_body_reference":
            return SeeDreamResult(
                url=None,
                skipped=False,
                error=str(exc),
                api_status="failed",
                fallback_used=False,
            )

        # For face_reference: try Reactor fallback
        if reactor_deferred and reference_image_url and reactor_client:
            logger.info(
                "[%s] SeeDream failed, applying deferred Reactor fallback", workflow_id
            )
            swapped = await _apply_reactor_fallback(
                target_url=first_frame_url,
                reference_url=reference_image_url,
                strength=face_swap_config.get("strength", 1.0),
                reactor_client=reactor_client,
                cos_client=cos_client,
                workflow_id=workflow_id,
            )
            if swapped:
                return SeeDreamResult(
                    url=swapped,
                    skipped=False,
                    face_swapped=True,
                    api_status="failed",
                    fallback_used=True,
                    fallback_reason="SeeDream failed, Reactor fallback applied",
                    error=str(exc),
                )

        # Final fallback: return original frame
        return SeeDreamResult(
            url=None,
            skipped=False,
            api_status="failed",
            fallback_used=True,
            fallback_reason="SeeDream failed, using original frame",
            error=str(exc),
        )
