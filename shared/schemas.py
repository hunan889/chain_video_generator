"""Shared lightweight schemas used by workflow_builder.

These are plain dataclasses (no pydantic dependency) so they can be used
by both api_gateway and gpu/comfyui_worker without pulling in heavy web framework deps.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LoraInput:
    """LoRA configuration for workflow injection.

    Field-compatible with api.models.schemas.LoraInput (pydantic version).
    workflow_builder functions accept both via duck typing (.name, .strength, etc.).
    """

    name: str
    strength: float = 0.8
    trigger_words: list[str] = field(default_factory=list)
    trigger_prompt: Optional[str] = None
