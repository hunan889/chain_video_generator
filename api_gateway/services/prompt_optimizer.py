"""Adapter: constructs PromptOptimizer from api.services without api.config dependency.

The gateway injects a fake api.config module into sys.modules so the real
PromptOptimizer class can be imported and instantiated with gateway-supplied values.
"""

import logging
import os
import sys
import types
from pathlib import Path
from typing import Optional

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


def make_prompt_optimizer(
    llm_api_key: str,
    llm_base_url: str,
    llm_model: str,
    vision_api_key: str,
    vision_base_url: str,
    vision_model: str,
):
    """Construct a PromptOptimizer with explicit config values."""
    _ensure_stubs(llm_api_key, llm_base_url, llm_model,
                  vision_api_key, vision_base_url, vision_model)

    from api.services.prompt_optimizer import PromptOptimizer
    return PromptOptimizer()
