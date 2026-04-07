"""Adapter that builds the OLD ``api.services.prompt_optimizer.PromptOptimizer``
and patches its low-level vLLM hooks to go through Redis instead of direct HTTP.

Why patch instead of rewrite?
    PromptOptimizer has ~1000 lines of carefully tuned NSFW prompt-engineering
    logic in methods like ``optimize``, ``generate_video_prompt``,
    ``refine_prompt_for_image``, ``generate_continuation_prompt``. They all
    funnel through two helpers — ``_llm_call(system, user, ...)`` and
    ``_describe_image(image_b64)`` — that originally hit vLLM via direct HTTP.
    By replacing just those two helpers we keep the prompt logic untouched and
    redirect every call through ``InferenceClient`` (Redis → gpu/inference_worker
    → loopback vLLM on 148).

Without this patch the prompt routes are dead, because vLLM on 148 binds
``127.0.0.1`` only and the gateway runs on a different box.
"""

import logging
import os
import re
import sys
import types
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Project root: parent of api_gateway/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROJECT_ROOT_PATH = Path(_PROJECT_ROOT)


def _ensure_stubs(
    llm_api_key: str,
    llm_base_url: str,
    llm_model: str,
    vision_api_key: str,
    vision_base_url: str,
    vision_model: str,
) -> None:
    """Insert project root into sys.path and stub api.config + api.models.schemas."""
    # Ensure the project root is in sys.path so api/ package is importable
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

    # Stub api.config so the real one (which loads ComfyUI env vars) is never executed
    fake_cfg = types.ModuleType("api.config")
    fake_cfg.LLM_API_KEY = llm_api_key
    fake_cfg.LLM_BASE_URL = llm_base_url
    fake_cfg.LLM_MODEL = llm_model
    fake_cfg.VISION_API_KEY = vision_api_key
    fake_cfg.VISION_BASE_URL = vision_base_url
    fake_cfg.VISION_MODEL = vision_model
    fake_cfg.PROJECT_ROOT = _PROJECT_ROOT_PATH
    fake_cfg.CIVITAI_API_TOKEN = ""
    sys.modules["api.config"] = fake_cfg

    # Stub api.models.schemas with the dataclasses civitai_client expects
    if "api.models.schemas" not in sys.modules:
        if "api.models" not in sys.modules:
            fake_models = types.ModuleType("api.models")
            fake_models.__path__ = [os.path.join(_PROJECT_ROOT, "api", "models")]
            sys.modules["api.models"] = fake_models

        from dataclasses import dataclass, field

        fake_schemas = types.ModuleType("api.models.schemas")

        @dataclass
        class CivitAIFile:
            name: str = ""
            size_mb: float = 0
            download_url: str = ""

        @dataclass
        class CivitAIModelVersion:
            id: int = 0
            name: str = ""
            trained_words: list = field(default_factory=list)
            download_url: str = ""
            base_model: str = ""
            file_size_mb: float = 0
            files: list = field(default_factory=list)

        @dataclass
        class CivitAIModelResult:
            id: int = 0
            name: str = ""
            description: str = ""
            tags: list = field(default_factory=list)
            preview_url: Optional[str] = None
            versions: list = field(default_factory=list)
            stats: dict = field(default_factory=dict)

        fake_schemas.CivitAIFile = CivitAIFile
        fake_schemas.CivitAIModelVersion = CivitAIModelVersion
        fake_schemas.CivitAIModelResult = CivitAIModelResult
        sys.modules["api.models.schemas"] = fake_schemas


def _clean_llm_text(text: str) -> str:
    """Apply the same post-processing the OLD ``_llm_call`` did.

    Strips ``<think>...</think>`` tags and unwraps a single ``\\u0060\\u0060\\u0060json``
    fenced block if present, so callers that ``json.loads`` the result keep working.
    """
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def _patch_for_redis(optimizer, redis, llm_model: str, vision_model: str) -> None:
    """Replace ``_llm_call`` and ``_describe_image`` on ``optimizer`` with
    InferenceClient-backed versions.

    The replacements have the same async signatures the OLD class exposed,
    so every higher-level method (``optimize``, ``generate_video_prompt``,
    ``generate_continuation_prompt``, ``refine_prompt_for_image``, …) keeps
    working without modification.
    """
    if redis is None:
        # No Redis available — leave the original implementation in place so
        # callers can still see the connect-refused error and degrade.
        logger.warning(
            "make_prompt_optimizer: no Redis connection supplied, "
            "PromptOptimizer will use its legacy direct-HTTP path"
        )
        return

    # Local import keeps the cold-start cost off the critical path
    from api_gateway.services.gpu_clients.inference import (
        InferenceClient,
        InferenceError,
        InferenceTimeout,
    )

    inference = InferenceClient(redis)

    async def _redis_llm_call(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.8,
    ) -> str:
        try:
            text = await inference.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=llm_model or None,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=120.0,
            )
        except (InferenceError, InferenceTimeout) as exc:
            logger.warning("PromptOptimizer LLM call via inference_worker failed: %s", exc)
            raise
        return _clean_llm_text(text)

    async def _redis_describe_image(self, image_base64: str) -> str:
        try:
            text = await inference.describe_image(
                image_b64=image_base64,
                model=vision_model or None,
                timeout=60.0,
            )
        except (InferenceError, InferenceTimeout) as exc:
            logger.warning(
                "PromptOptimizer VLM call via inference_worker failed: %s", exc
            )
            raise
        return _clean_llm_text(text)

    # Bind as instance methods so ``self`` resolves correctly
    import types as _types
    optimizer._llm_call = _types.MethodType(_redis_llm_call, optimizer)
    optimizer._describe_image = _types.MethodType(_redis_describe_image, optimizer)
    # Also override the URL fields so any code that logs them shows where
    # work is actually going.
    optimizer.url = "redis://queue:inference (chat)"
    optimizer.vision_url = "redis://queue:inference (describe_image)"


def make_prompt_optimizer(
    llm_api_key: str,
    llm_base_url: str,
    llm_model: str,
    vision_api_key: str,
    vision_base_url: str,
    vision_model: str,
    redis: Any = None,
):
    """Construct a PromptOptimizer with explicit config values.

    When ``redis`` is supplied (the normal gateway case), low-level LLM and
    VLM calls are redirected through Redis → gpu/inference_worker so they
    work even when the vLLM endpoints on 148 are bound to ``127.0.0.1``
    and unreachable from the gateway box.

    When ``redis`` is None, falls back to the legacy direct-HTTP path
    (kept only for unit tests / scripts that don't need Redis).
    """
    _ensure_stubs(llm_api_key, llm_base_url, llm_model,
                  vision_api_key, vision_base_url, vision_model)

    from api.services.prompt_optimizer import PromptOptimizer
    optimizer = PromptOptimizer()
    _patch_for_redis(optimizer, redis, llm_model, vision_model)
    return optimizer
