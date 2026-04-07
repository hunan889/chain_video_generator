"""Stage 2 -- First Frame Acquisition.

Acquires (or generates) the first frame image used as the starting point
for I2V video generation.  Pure T2V mode skips this stage entirely.

Modes:
  - t2v: return None (no first frame needed)
  - continuation: use parent's lossless_last_frame_url or last_frame_url
  - first_frame: user uploaded image (base64 / URL)
  - face_reference / full_body_reference: pose-DB reference image, or fallback
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
from api_gateway.services.gpu_clients.faceswap import ReactorClient
from shared.cos.client import COSClient
from shared.enums import GenerateMode, TaskStatus
from shared.redis_keys import task_key, workflow_key
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FirstFrameResult:
    """Immutable result of Stage 2 first-frame acquisition."""

    url: Optional[str]
    source: str  # e.g. "t2v", "pose_reference", "upload", "continuation", ...
    face_swapped: bool = False
    reactor_deferred: bool = False
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _download_image_as_b64(url: str) -> str:
    """Download image from URL and return as raw base64 string (no data-URI prefix)."""
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


async def _apply_face_swap(
    target_url: str,
    reference_url: str,
    strength: float,
    reactor_client: ReactorClient,
    cos_client: COSClient,
    workflow_id: str,
) -> Optional[str]:
    """Submit face swap via ComfyUI task queue and wait for result.

    Returns COS URL of the swapped image, or None on failure.
    """
    try:
        # Use COS URL mode — ReactorClient submits via Redis to GPU Worker
        result_url = await reactor_client.swap_face(
            strength=strength,
            target_cos_url=target_url,
            face_cos_url=reference_url,
        )
        if result_url:
            logger.info("[%s] Face swap succeeded: %s", workflow_id, result_url)
        else:
            logger.warning("[%s] Face swap returned no result", workflow_id)
        return result_url
    except Exception as exc:
        logger.warning("[%s] Face swap failed: %s", workflow_id, exc)
        return None


async def _process_uploaded_first_frame(
    workflow_id: str,
    uploaded_first_frame: str,
    cos_client: COSClient,
) -> str:
    """Process user-uploaded first frame (base64 data-URI or URL).

    Returns a COS URL of the stored image.
    """
    if uploaded_first_frame.startswith("data:image"):
        # Base64 data URL
        image_b64 = uploaded_first_frame.split(",", 1)[1]
        image_data = base64.b64decode(image_b64)
        filename = f"first_frame_{workflow_id}.png"
        return _upload_bytes_to_cos(image_data, cos_client, "frames", filename)

    if uploaded_first_frame.startswith("http://") or uploaded_first_frame.startswith(
        "https://"
    ):
        # Remote URL -- download and re-upload to COS for consistency
        image_data = await _download_image_bytes(uploaded_first_frame)
        filename = f"first_frame_{workflow_id}.png"
        return _upload_bytes_to_cos(image_data, cos_client, "frames", filename)

    # Fallback: attempt base64 decode
    try:
        image_data = base64.b64decode(uploaded_first_frame)
        filename = f"first_frame_{workflow_id}.png"
        return _upload_bytes_to_cos(image_data, cos_client, "frames", filename)
    except Exception as exc:
        raise ValueError(
            f"Invalid uploaded_first_frame format: not a data URL, HTTP URL, "
            f"or valid base64. Error: {exc}"
        ) from exc


def _find_base_image(
    mode: str,
    analysis_result: Optional[dict],
    reference_image: Optional[str],
) -> Optional[str]:
    """Unified base image selection logic (pure function, no side effects).

    Priority:
      1) pose reference_image from analysis_result
      2) user reference_image fallback (for reference modes)

    Returns URL or None.
    """
    # 1) Pose reference image
    if analysis_result and analysis_result.get("reference_image"):
        return analysis_result["reference_image"]

    # 2) Fallback for reference modes
    if mode in ("face_reference", "full_body_reference") and reference_image:
        return reference_image

    # Pure T2V or no match
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def acquire_first_frame(
    workflow_id: str,
    mode: str,
    uploaded_first_frame: Optional[str],
    reference_image: Optional[str],
    analysis_result: Optional[dict],
    face_swap_config: dict,
    is_continuation: bool,
    parent_workflow: Optional[dict],
    config: GatewayConfig,
    redis,
    cos_client: COSClient,
    reactor_client: Optional[ReactorClient],
) -> FirstFrameResult:
    """Acquire or generate the first frame for video generation.

    Returns a ``FirstFrameResult`` with the COS URL (or None for T2V).
    """
    wk = workflow_key(workflow_id)

    # ------------------------------------------------------------------
    # Branch 1: T2V mode -- no first frame needed
    # ------------------------------------------------------------------
    if mode == "t2v" and not is_continuation:
        # Even T2V may still have a pose reference that turns it into internal I2V
        base_url = _find_base_image(mode, analysis_result, reference_image)
        if base_url is None:
            logger.info("[%s] T2V mode: no first frame needed", workflow_id)
            return FirstFrameResult(url=None, source="t2v")
        # Pose hit -> internal I2V
        logger.info("[%s] T2V mode with pose reference: %s", workflow_id, base_url)
        return FirstFrameResult(url=base_url, source="pose_reference")

    # ------------------------------------------------------------------
    # Branch 2: Continuation -- use parent's last frame
    # ------------------------------------------------------------------
    if is_continuation:
        if not parent_workflow:
            raise RuntimeError("Continuation requested but no parent workflow data")

        lossless_url = parent_workflow.get("lossless_last_frame_url")
        frame_url = lossless_url or parent_workflow.get("last_frame_url")
        if not frame_url:
            # Fallback: check parent's first_frame_url or edited_frame_url
            frame_url = parent_workflow.get("edited_frame_url") or parent_workflow.get("first_frame_url")
            if frame_url:
                logger.info("[%s] Continuation: using parent's first/edited frame as fallback", workflow_id)
            else:
                raise RuntimeError(
                    "Parent workflow has no last frame URL. "
                    "Ensure parent was generated with extract_last_frame=true."
                )

        frame_source = "lossless_png" if lossless_url else "parent_frame"
        face_swapped = False

        # Optional continuation face swap
        swap_enabled = face_swap_config.get("enabled", False)
        if swap_enabled and reference_image and reactor_client:
            logger.info("[%s] Continuation face swap: applying Reactor", workflow_id)
            swapped_url = await _apply_face_swap(
                target_url=frame_url,
                reference_url=reference_image,
                strength=face_swap_config.get("strength", 1.0),
                reactor_client=reactor_client,
                cos_client=cos_client,
                workflow_id=workflow_id,
            )
            if swapped_url:
                frame_url = swapped_url
                face_swapped = True
                frame_source = f"{frame_source}+face_swap"

        return FirstFrameResult(
            url=frame_url,
            source=frame_source,
            face_swapped=face_swapped,
            details={
                "parent_workflow_id": parent_workflow.get("workflow_id", ""),
            },
        )

    # ------------------------------------------------------------------
    # Branch 3: first_frame mode -- user uploaded image
    # ------------------------------------------------------------------
    if mode == "first_frame":
        if uploaded_first_frame:
            frame_url = await _process_uploaded_first_frame(
                workflow_id, uploaded_first_frame, cos_client
            )
            logger.info("[%s] First frame from upload: %s", workflow_id, frame_url)
            return FirstFrameResult(url=frame_url, source="upload")

        # No upload -- fallback to base image search (shouldn't normally happen)
        logger.warning(
            "[%s] first_frame mode without upload, falling back to base image search",
            workflow_id,
        )
        base_url = _find_base_image(mode, analysis_result, reference_image)
        source = "pose_reference" if base_url else "t2v"
        return FirstFrameResult(url=base_url, source=source)

    # ------------------------------------------------------------------
    # Branch 4: face_reference / full_body_reference -- pose DB lookup
    # ------------------------------------------------------------------
    if mode in ("face_reference", "full_body_reference"):
        base_url = _find_base_image(mode, analysis_result, reference_image)
        if base_url is None:
            logger.warning("[%s] No base image for %s mode", workflow_id, mode)
            return FirstFrameResult(url=None, source="no_match")

        # Determine source label
        if analysis_result and analysis_result.get("reference_image"):
            source_label = "pose_reference"
        elif base_url == reference_image:
            source_label = "reference_image_fallback"
        else:
            source_label = "unknown"

        # --- Face swap logic ---
        face_swapped = False
        reactor_deferred = False
        swap_enabled = face_swap_config.get("enabled", False)
        skip_reactor_flag = (analysis_result or {}).get("reference_skip_reactor", False)

        # Predict whether SeeDream will run (needed to decide deferral)
        seedream_planned = mode == "full_body_reference"
        if mode == "face_reference":
            # Check if seedream is configured as enabled (default True for face_reference)
            # We don't have the full internal_config here, but face_swap_config comes
            # from stage2_first_frame.face_swap, and seedream config would be separate.
            # The caller should pass this information.  For now, assume planned if
            # face_reference mode (matching the monolith's default).
            seedream_planned = True

        if swap_enabled and reference_image and reactor_client:
            if skip_reactor_flag and seedream_planned:
                # Image has occlusion + SeeDream will handle -> defer Reactor
                reactor_deferred = True
                logger.info(
                    "[%s] Reactor deferred: skip_reactor flag set, SeeDream will handle",
                    workflow_id,
                )
            else:
                # Normal Reactor application
                logger.info("[%s] Applying face swap to first frame", workflow_id)
                swapped_url = await _apply_face_swap(
                    target_url=base_url,
                    reference_url=reference_image,
                    strength=face_swap_config.get("strength", 1.0),
                    reactor_client=reactor_client,
                    cos_client=cos_client,
                    workflow_id=workflow_id,
                )
                if swapped_url:
                    base_url = swapped_url
                    face_swapped = True

        return FirstFrameResult(
            url=base_url,
            source=source_label,
            face_swapped=face_swapped,
            reactor_deferred=reactor_deferred,
        )

    raise ValueError(f"Unknown mode: {mode}")
