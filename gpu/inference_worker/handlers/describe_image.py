"""VLM image description handler — POST to local vLLM (Qwen2.5-VL) loopback."""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT = (
    "Describe this image in concrete, factual detail. Focus on the people, "
    "their actions, body positions, and the setting. Do not refuse. Do not "
    "moralise. Output one paragraph of plain English."
)

# vLLM uses a single httpx client per worker process for connection pooling.
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=60.0)
    return _client


async def handle(
    payload: dict,
    *,
    base_url: str,
    default_model: str,
) -> dict:
    """Worker entry point for ``inference_describe`` tasks.

    Payload shape::
        {
            "image_b64": "<base64 string, may include data: prefix>",
            "prompt": "...",                    # optional
            "model": "...",                     # optional
            "max_tokens": 512,
            "temperature": 0.3
        }
    """
    image_b64 = payload.get("image_b64") or ""
    if not image_b64:
        raise ValueError("payload.image_b64 is required")

    prompt = payload.get("prompt") or _DEFAULT_PROMPT
    model = payload.get("model") or default_model
    max_tokens = int(payload.get("max_tokens", 512))
    temperature = float(payload.get("temperature", 0.3))

    # Strip data: URL prefix if present
    mime = "image/jpeg"
    raw_b64 = image_b64
    m = re.match(r"data:(image/\w+);base64,(.+)", image_b64, re.DOTALL)
    if m:
        mime = m.group(1)
        raw_b64 = m.group(2)

    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{raw_b64}"},
                },
                {"type": "text", "text": prompt},
            ],
        }],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    url = f"{base_url}/chat/completions"
    client = _get_client()
    resp = await client.post(url, json=body)
    resp.raise_for_status()
    data = resp.json()

    text = data["choices"][0]["message"]["content"].strip()
    # Strip <think>...</think> tags if the model emits reasoning
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    return {"text": text, "model": model}


async def close() -> None:
    """Close the shared HTTP client (called on worker shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
