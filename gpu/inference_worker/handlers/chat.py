"""LLM chat handler — POST to local vLLM (Qwen3-14B) loopback.

Used by pose_recommender for LLM rerank, and reusable by other gateway code
that needs an OpenAI-format chat completion.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Per-worker httpx client for connection pooling
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
    """Worker entry point for ``inference_chat`` tasks.

    Payload shape::
        {
            "messages": [{"role": "system|user|assistant", "content": "..."}],
            "model": "...",                  # optional
            "max_tokens": 512,
            "temperature": 0.1,
            "response_format": "json_object" # optional
        }
    """
    messages = payload.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise ValueError("payload.messages must be a non-empty list")

    model = payload.get("model") or default_model
    max_tokens = int(payload.get("max_tokens", 512))
    temperature = float(payload.get("temperature", 0.1))

    body: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if payload.get("response_format"):
        body["response_format"] = {"type": payload["response_format"]}

    url = f"{base_url}/chat/completions"
    client = _get_client()
    resp = await client.post(url, json=body)
    resp.raise_for_status()
    data = resp.json()

    text = data["choices"][0]["message"]["content"].strip()
    # Strip <think>...</think> tags if the model emits reasoning
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    return {
        "text": text,
        "model": model,
        "usage": data.get("usage", {}),
    }


async def close() -> None:
    """Close the shared HTTP client (called on worker shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
