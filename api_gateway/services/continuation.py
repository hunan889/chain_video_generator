"""VLM + LLM helpers for auto-continue chain orchestration.

Used by ``chain_orchestrator.py`` to compute the prompt for segment N+1
based on the last frame of segment N. All GPU work goes through
``InferenceClient`` (Redis → ``gpu/inference_worker`` → loopback vLLM on
148) so the gateway never needs direct network reachability to the vLLM
endpoints.

Both functions are graceful: on any failure they return ``None`` and the
caller is expected to fall back to whatever pre-set prompt the user
supplied for that segment.
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

import aiohttp

from api_gateway.services.gpu_clients.inference import (
    InferenceClient,
    InferenceError,
    InferenceTimeout,
)

logger = logging.getLogger(__name__)


# System / instruction prompts that used to be wired into the openai SDK
# directly. Kept here so the helpers stay self-contained.
_VLM_INSTRUCTION = (
    "Describe this image in 1-2 sentences focusing on: subject position, "
    "lighting, environment, and any motion cues. Be concise and factual."
)

_LLM_SYSTEM = (
    "/no_think You are a creative video director. Given a description of "
    "the last frame of a video segment, write a SHORT continuation prompt "
    "(≤30 words) for the NEXT segment. The continuation should feel like "
    "a natural, cinematic follow-on. Output only the prompt text, no "
    "explanations, no thinking tags."
)


async def _download_to_b64(image_url: str, *, timeout: float = 15.0) -> Optional[str]:
    """Fetch an image URL and return its raw base64 (no data: prefix)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                image_url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "describe_frame: GET %s returned HTTP %d",
                        image_url[:80], resp.status,
                    )
                    return None
                data = await resp.read()
                return base64.b64encode(data).decode("ascii")
    except Exception as exc:
        logger.warning("describe_frame: failed to download %s: %s", image_url[:80], exc)
        return None


async def describe_frame(
    image_url: str,
    *,
    redis,
    model: str = "",
    # Legacy positional args kept for source-compatibility with the old
    # signature (api_key/base_url were openai SDK params). Ignored.
    api_key: str = "",
    base_url: str = "",
) -> Optional[str]:
    """Use VLM (Qwen2.5-VL via inference_worker) to describe ``image_url``.

    Returns the description string, or ``None`` on failure (image fetch
    failed, worker unreachable, etc).
    """
    if not image_url:
        return None
    if redis is None:
        logger.warning("describe_frame: no redis connection, cannot call inference_worker")
        return None

    raw_b64 = await _download_to_b64(image_url)
    if not raw_b64:
        return None

    try:
        client = InferenceClient(redis)
        text = await client.describe_image(
            image_b64=raw_b64,
            prompt=_VLM_INSTRUCTION,
            model=model or None,
            max_tokens=200,
            temperature=0.3,
            timeout=60.0,
        )
        text = (text or "").strip()
        logger.debug("Frame description: %s", text)
        return text or None
    except (InferenceError, InferenceTimeout) as exc:
        logger.warning("describe_frame: VLM call failed: %s", exc)
        return None
    except Exception as exc:
        logger.exception("describe_frame: unexpected error: %s", exc)
        return None


async def generate_continuation_prompt(
    frame_description: str,
    previous_prompt: str,
    *,
    redis,
    model: str = "",
    # Legacy kwargs kept for compatibility with the old signature
    api_key: str = "",
    base_url: str = "",
) -> Optional[str]:
    """Ask the LLM (Qwen3-14B via inference_worker) for the next-segment prompt.

    Returns the continuation prompt string, or ``None`` on failure.
    """
    if not frame_description:
        return None
    if redis is None:
        logger.warning("generate_continuation_prompt: no redis connection")
        return None

    user_msg = (
        f"Previous segment prompt: {previous_prompt}\n"
        f"Last frame description: {frame_description}\n"
        "Write the continuation prompt for the next segment."
    )

    try:
        client = InferenceClient(redis)
        text = await client.chat(
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            model=model or None,
            max_tokens=100,
            temperature=0.7,
            timeout=30.0,
        )
        text = (text or "").strip()
        logger.debug("Continuation prompt: %s", text)
        return text or None
    except (InferenceError, InferenceTimeout) as exc:
        logger.warning("generate_continuation_prompt: LLM call failed: %s", exc)
        return None
    except Exception as exc:
        logger.exception("generate_continuation_prompt: unexpected error: %s", exc)
        return None
