"""Inference client — submits embed/describe/chat jobs to gpu/inference_worker via Redis.

Mirrors the ``faceswap.py`` pattern: write a ``task:<id>`` HASH, RPUSH onto
``queue:inference``, poll until completion. The worker reads the ``mode`` field
to dispatch to the right handler (BGE / VLM HTTP / LLM HTTP, all loopback on 148).

Usage::

    client = InferenceClient(redis=redis_conn)

    # 1) Embed text(s) — returns list[list[float]]
    vectors = await client.embed(["oral sex", "blowjob"])

    # 2) Describe an image — returns text caption
    desc = await client.describe_image(image_b64=img_b64)

    # 3) Chat / rerank — returns LLM response text
    answer = await client.chat([
        {"role": "system", "content": "You are a pose classifier."},
        {"role": "user", "content": "..."}
    ])

All methods raise :class:`InferenceError` on worker failure or
:class:`InferenceTimeout` if the worker doesn't respond in time.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from shared.enums import GenerateMode
from shared.redis_keys import inference_queue_key

from api_gateway.services.gpu_clients.base import (
    GpuClientError,
    GpuClientTimeout,
    decode_result_field,
    submit_and_wait,
)

logger = logging.getLogger(__name__)


class InferenceError(GpuClientError):
    """gpu/inference_worker reported a failure."""


class InferenceTimeout(GpuClientTimeout):
    """gpu/inference_worker did not respond in time."""


# Default timeouts (seconds). Embedding is fastest; LLM rerank is slowest.
DEFAULT_EMBED_TIMEOUT = 15.0
DEFAULT_DESCRIBE_TIMEOUT = 60.0
DEFAULT_CHAT_TIMEOUT = 30.0
DEFAULT_POLL_INTERVAL = 0.2  # 200 ms — embedding finishes in ~50 ms


class InferenceClient:
    """Redis-based client for the inference worker pool on 148.

    Stateless apart from the Redis connection. Safe to instantiate per-request
    or share across the gateway process.
    """

    def __init__(self, redis) -> None:
        self._redis = redis

    # ------------------------------------------------------------------
    # 1. Embed
    # ------------------------------------------------------------------
    async def embed(
        self,
        texts: list[str],
        *,
        model: str = "bge-large-zh-v1.5",
        normalize: bool = True,
        timeout: float = DEFAULT_EMBED_TIMEOUT,
    ) -> list[list[float]]:
        """Embed one or more texts. Returns a list of float vectors.

        Vectors are L2-normalized by default so cosine similarity reduces to
        a dot product (cheap on the gateway side).
        """
        if not texts:
            return []

        payload = {
            "texts": list(texts),
            "model": model,
            "normalize": normalize,
        }
        raw = await self._submit(
            mode=GenerateMode.INFERENCE_EMBED,
            payload=payload,
            timeout=timeout,
        )
        result = decode_result_field(raw)
        if not isinstance(result, dict) or "vectors" not in result:
            raise InferenceError(f"embed: unexpected result shape: {result!r}")
        vectors = result["vectors"]
        if not isinstance(vectors, list):
            raise InferenceError(f"embed: vectors not a list: {type(vectors)}")
        return vectors

    # ------------------------------------------------------------------
    # 2. Describe image (VLM)
    # ------------------------------------------------------------------
    async def describe_image(
        self,
        image_b64: str,
        *,
        prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.3,
        timeout: float = DEFAULT_DESCRIBE_TIMEOUT,
    ) -> str:
        """Describe an image via the VLM (Qwen2.5-VL by default).

        ``prompt`` overrides the default "describe this image" instruction.
        ``model`` overrides the worker default and lets the caller pick a
        specific VLM model name (passed straight to the OpenAI-compat /v1).
        """
        if not image_b64:
            raise InferenceError("describe_image: image_b64 is required")

        payload: dict[str, Any] = {
            "image_b64": image_b64,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if prompt is not None:
            payload["prompt"] = prompt
        if model is not None:
            payload["model"] = model

        raw = await self._submit(
            mode=GenerateMode.INFERENCE_DESCRIBE,
            payload=payload,
            timeout=timeout,
        )
        result = decode_result_field(raw)
        if isinstance(result, dict) and "text" in result:
            return str(result["text"])
        if isinstance(result, str):
            return result
        raise InferenceError(f"describe_image: unexpected result shape: {result!r}")

    # ------------------------------------------------------------------
    # 3. Chat / rerank (LLM)
    # ------------------------------------------------------------------
    async def chat(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.1,
        response_format: Optional[str] = None,
        timeout: float = DEFAULT_CHAT_TIMEOUT,
    ) -> str:
        """Send an OpenAI-format chat completion to the LLM.

        Returns just the assistant's text content. Caller is responsible for
        any further parsing (JSON arrays for rerank, etc).
        """
        if not messages:
            raise InferenceError("chat: messages list is required")

        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if model is not None:
            payload["model"] = model
        if response_format is not None:
            payload["response_format"] = response_format

        raw = await self._submit(
            mode=GenerateMode.INFERENCE_CHAT,
            payload=payload,
            timeout=timeout,
        )
        result = decode_result_field(raw)
        if isinstance(result, dict) and "text" in result:
            return str(result["text"])
        if isinstance(result, str):
            return result
        raise InferenceError(f"chat: unexpected result shape: {result!r}")

    # ------------------------------------------------------------------
    # Internal: shared submit-and-poll
    # ------------------------------------------------------------------
    async def _submit(
        self,
        *,
        mode: GenerateMode,
        payload: dict,
        timeout: float,
    ) -> dict:
        task_data = {
            "mode": mode.value,
            "payload": json.dumps(payload),
            "result": "",
            "error": "",
        }
        return await submit_and_wait(
            self._redis,
            queue_name=inference_queue_key(),
            task_data=task_data,
            timeout=timeout,
            poll_interval=DEFAULT_POLL_INTERVAL,
            error_class=InferenceError,
            timeout_class=InferenceTimeout,
        )
