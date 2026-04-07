"""Face swap client — submits ReActor face swap jobs via Redis.

The actual face swap runs as a ComfyUI workflow on the GPU box (148),
consumed by ``gpu/comfyui_worker``. From the gateway's perspective this is
just another Redis-queued job: write a ``task:<id>`` HASH, RPUSH onto
``queue:faceswap``, poll until completion.

Previously located at ``api_gateway/services/external/reactor.py``;
moved here as part of the ``api_gateway/services/gpu_clients/`` consolidation.
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

# Image faceswap workflow template — uses ReActorFaceSwap (GPU-accelerated).
# Previously used mtb Face Swap nodes which ran on CPU (~90s).
# ReActor uses CUDA and completes in ~10s.
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
        "class_type": "ReActorFaceSwap",
        "inputs": {
            "enabled": True,
            "input_image": ["1", 0],
            "source_image": ["2", 0],
            "swap_model": "inswapper_128.onnx",
            "facedetection": "retinaface_resnet50",
            "face_restore_model": "codeformer-v0.1.0.pth",
            "face_restore_visibility": 0.85,
            "codeformer_weight": 0.3,
            "detect_gender_input": "no",
            "detect_gender_source": "no",
            "input_faces_index": "0",
            "source_faces_index": "0",
            "console_log_level": 1,
        },
    },
    "4": {
        "class_type": "SaveImage",
        "inputs": {"images": ["3", 0], "filename_prefix": "faceswap"},
    },
}

_MAX_WAIT = 120  # seconds
_POLL_INTERVAL = 1  # seconds
_MAX_RETRIES = 2   # retry once on failure (total 2 attempts)


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

        Retries automatically on failure (e.g. ComfyUI instance down).
        Returns COS URL of result, or None on failure.
        """
        if not self.redis:
            logger.warning("ReactorClient: no Redis connection, cannot submit face swap")
            return None

        if not target_cos_url or not face_cos_url:
            logger.warning("ReactorClient: COS URLs required for ComfyUI-based face swap")
            return None

        last_error = None
        for attempt in range(1, _MAX_RETRIES + 1):
            result = await self._submit_and_wait(
                target_cos_url, face_cos_url, strength, attempt,
            )
            if result is not None:
                return result
            if attempt < _MAX_RETRIES:
                logger.warning("Face swap attempt %d failed, retrying...", attempt)
                await asyncio.sleep(2)  # brief pause before retry

        logger.error("Face swap failed after %d attempts", _MAX_RETRIES)
        return None

    async def _submit_and_wait(
        self,
        target_cos_url: str,
        face_cos_url: str,
        strength: float,
        attempt: int,
    ) -> Optional[str]:
        """Submit one face swap task and poll until completion."""
        task_id = uuid.uuid4().hex

        workflow = json.loads(json.dumps(_FACESWAP_WORKFLOW))
        workflow["3"]["inputs"]["console_log_level"] = hash(task_id) % 2
        workflow["4"]["inputs"]["filename_prefix"] = f"faceswap_{task_id[:8]}"

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

        task_data = {
            "status": "queued",
            "mode": GenerateMode.FACESWAP.value,
            "model": "faceswap",
            "workflow": json.dumps(workflow),
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
        await self.redis.rpush(queue_key("faceswap"), task_id)

        logger.info("Submitted face swap task %s attempt %d (target=%s face=%s)",
                     task_id, attempt, target_cos_url[:50], face_cos_url[:50])

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
                return None  # will trigger retry in caller

        logger.error("Face swap %s timed out after %ds", task_id, _MAX_WAIT)
        return None
