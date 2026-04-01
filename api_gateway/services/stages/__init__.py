"""Workflow engine stages for the API gateway.

Each stage is a self-contained module with a single public async entry point
that accepts explicit dependencies (no global state) and returns an immutable
result dataclass.

Stages:
  1. prompt_analysis  (Phase 1 -- already exists)
  2. first_frame      (Phase 3) -- acquire/generate the first frame
  3. seedream_edit    (Phase 4) -- edit first frame via SeeDream
  4. video_generation (Phase 5) -- submit video task and poll for completion
"""

from api_gateway.services.stages.first_frame import (
    FirstFrameResult,
    acquire_first_frame,
)
from api_gateway.services.stages.seedream_edit import (
    SeeDreamResult,
    build_seedream_prompt,
    edit_first_frame,
)
from api_gateway.services.stages.video_generation import (
    VideoGenerationResult,
    generate_video,
    parse_resolution,
)

__all__ = [
    # Stage 2
    "FirstFrameResult",
    "acquire_first_frame",
    # Stage 3
    "SeeDreamResult",
    "build_seedream_prompt",
    "edit_first_frame",
    # Stage 4
    "VideoGenerationResult",
    "generate_video",
    "parse_resolution",
]
