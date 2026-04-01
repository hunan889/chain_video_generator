"""Reactor face swap — via ComfyUI task queue (Redis).

Instead of calling Forge HTTP directly (which requires network access to GPU server),
we submit a face swap workflow to the GPU Worker via Redis, wait for completion,
and return the result image URL from COS.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

from shared.enums import GenerateMode, ModelType
from shared.redis_keys import task_key, queue_key

logger = logging.getLogger(__name__)

# Image faceswap workflow template (uses mtb nodes available in ComfyUI)
_FACESWAP_WORKFLOW = {
    "1": {
        "class_type": "LoadImage",
        "inputs": {"image": "__TARGET_IMAGE__"},
    },
    "2": {
        "class_type": "LoadImage",
        "inputs": {"image": "__FACE_IMAGE__"},
    },
    "3": {
        "class_type": "Load Face Swap Model (mtb)",
        "inputs": {"faceswap_model": "inswapper_128.onnx"},
    },
    "4": {
        "class_type": "Load Face Analysis Model (mtb)",
        "inputs": {"faceanalysis_model": "buffalo_l"},
    },
    "5": {
        "class_type": "Face Swap (mtb)",
        "inputs": {
            "image": ["1", 0],
            "reference": ["2", 0],
            "swapper_model": ["3", 0],
            "faceanalysis_model": ["4", 0],
            "faces_index": "0",
        },
    },
    "6": {
        "class_type": "SaveImage",
        "inputs": {"images": ["5", 0], "filename_prefix": "faceswap"},
    },
}

_MAX_WAIT = 120  # seconds
_POLL_INTERVAL = 3  # seconds


class ReactorClient:
    """Face swap via ComfyUI Reactor/mtb nodes through Redis task queue."""

    def __init__(self, gateway=None, redis=None, cos_prefix: str = "cvid", **kwargs):
        """Initialize with Gateway's TaskGateway and Redis.

        Also accepts legacy `forge_url` kwarg for backward compat (ignored).
        """
        self.gateway = gateway
        self.redis = redis
        self.cos_prefix = cos_prefix

    async def swap_face(
        self,
        source_image_b64: str = "",
        target_image_b64: str = "",
        strength: float = 1.0,
        *,
        target_cos_url: str = "",
        face_cos_url: str = "",
    ) -> Optional[str]:
        """Submit a face swap task via ComfyUI and wait for result.

        Accepts either base64 images (legacy compat) or COS URLs (preferred).
        Returns COS URL of result, or None on failure.
        """
        if not self.redis:
            logger.warning("ReactorClient: no Redis connection, cannot submit face swap")
            return None

        # For COS URL mode (preferred in Gateway architecture)
        if not target_cos_url or not face_cos_url:
            logger.warning("ReactorClient: COS URLs required for ComfyUI-based face swap")
            return None

        task_id = uuid.uuid4().hex

        def _strip_to_cos_key(url: str) -> str:
            key = url
            if "://" in key:
                key = key.split("/", 3)[-1] if key.count("/") >= 3 else key
            if self.cos_prefix and key.startswith(self.cos_prefix + "/"):
                key = key[len(self.cos_prefix) + 1:]
            return key

        input_files = [
            {
                "cos_key": _strip_to_cos_key(target_cos_url),
                "cos_url": target_cos_url,
                "placeholder": "__TARGET_IMAGE__",
                "original_filename": "target.png",
            },
            {
                "cos_key": _strip_to_cos_key(face_cos_url),
                "cos_url": face_cos_url,
                "placeholder": "__FACE_IMAGE__",
                "original_filename": "face.png",
            },
        ]

        # Create task in Redis
        task_data = {
            "status": "queued",
            "mode": GenerateMode.FACESWAP.value,
            "model": ModelType.A14B.value,
            "workflow": json.dumps(_FACESWAP_WORKFLOW),
            "params": json.dumps({"type": "image_faceswap", "strength": strength}),
            "progress": "0",
            "video_url": "",
            "error": "",
            "created_at": str(int(time.time())),
            "input_files": json.dumps(input_files),
        }
        tk = task_key(task_id)
        await self.redis.hset(tk, mapping=task_data)
        await self.redis.expire(tk, 3600)
        await self.redis.rpush(queue_key(ModelType.A14B.value), task_id)

        logger.info("Submitted face swap task %s (target=%s face=%s)",
                     task_id, target_cos_url[:50], face_cos_url[:50])

        # Poll for completion
        deadline = time.time() + _MAX_WAIT
        while time.time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            raw = await self.redis.hgetall(tk)
            status = raw.get("status", "")
            if status == "completed":
                result_url = raw.get("video_url", "")
                if result_url:
                    logger.info("Face swap %s completed: %s", task_id, result_url)
                    return result_url
                logger.warning("Face swap %s completed but no result URL", task_id)
                return None
            elif status == "failed":
                error = raw.get("error", "Unknown")
                logger.error("Face swap %s failed: %s", task_id, error)
                return None

        logger.error("Face swap %s timed out after %ds", task_id, _MAX_WAIT)
        return None
