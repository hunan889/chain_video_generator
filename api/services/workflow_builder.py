"""Backward-compatible re-export from shared.workflow_builder.

Automatically configures paths from api.config on import so existing
code that does ``from api.services.workflow_builder import build_workflow``
continues to work without changes.
"""

from api.config import WORKFLOWS_DIR, COMFYUI_PATH, LORAS_PATH  # noqa: F401
from shared.workflow_builder import configure

# Auto-configure with api.config paths on import
configure(
    workflows_dir=WORKFLOWS_DIR,
    comfyui_path=COMFYUI_PATH,
    loras_path=LORAS_PATH,
)

# Re-export everything from shared.workflow_builder
from shared.workflow_builder import *  # noqa: F401, F403, E402

# Explicit re-exports for names that start with underscore (not covered by *)
from shared.workflow_builder import (  # noqa: E402
    _inject_story_postproc,
    _inject_lossless_frame_save,
    _inject_trigger_words,
    _load_template,
    _load_lora_name_map,
    _find_lora_file,
    _inject_loras,
    _inject_reactor,
    _inject_upscale,
    _bypass_color_match,
    _inject_story_loras,
    _calc_upscaled_size,
    _select_rife_profile,
    _load_lora_id_map,
    _build_lora_id_cache,
    _load_lora_keywords,
    _has_conflict,
    _has_variant_tag,
    _lora_name_map,
)
