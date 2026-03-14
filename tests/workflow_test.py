"""
Stage-by-stage testing endpoints for Advanced Workflow.

These endpoints allow testing each stage independently with controlled inputs.
"""
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from api.routes.workflow_executor import (
    _analyze_prompt,
    _acquire_first_frame,
    _apply_face_swap_to_frame,
    _get_config
)
from api.services.task_manager import TaskManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/workflow/test", tags=["workflow-testing"])


# ============================================================================
# Stage 1: Prompt Analysis Test
# ============================================================================

class Stage1TestRequest(BaseModel):
    """Test Stage 1: Prompt Analysis"""
    user_prompt: str = Field(..., description="User's original prompt")
    auto_analyze: bool = Field(default=True, description="Enable prompt analysis")
    auto_lora: bool = Field(default=True, description="Enable LoRA recommendation")
    auto_prompt: bool = Field(default=True, description="Enable prompt optimization")
    top_k_image_loras: int = Field(default=5, ge=1, le=20)
    top_k_video_loras: int = Field(default=5, ge=1, le=20)


class Stage1TestResponse(BaseModel):
    """Stage 1 output"""
    success: bool
    original_prompt: str
    optimized_t2i_prompt: Optional[str] = None
    optimized_i2v_prompt: Optional[str] = None
    image_loras: list = []
    video_loras: list = []
    error: Optional[str] = None


@router.post("/stage1", response_model=Stage1TestResponse)
async def test_stage1_prompt_analysis(
    req: Stage1TestRequest,
    task_manager: TaskManager = Depends(lambda: TaskManager())
):
    """
    Test Stage 1: Prompt Analysis

    Entry criteria:
    - user_prompt: non-empty string

    Exit criteria:
    - optimized_t2i_prompt: string (if auto_prompt=true)
    - optimized_i2v_prompt: string (if auto_prompt=true)
    - image_loras: array with length <= top_k_image_loras
    - video_loras: array with length <= top_k_video_loras
    """
    try:
        if not req.auto_analyze:
            return Stage1TestResponse(
                success=True,
                original_prompt=req.user_prompt,
                optimized_t2i_prompt=None,
                optimized_i2v_prompt=None,
                image_loras=[],
                video_loras=[]
            )

        # Create a mock request object with required fields
        class MockRequest:
            def __init__(self):
                self.user_prompt = req.user_prompt
                self.mode = "face_reference"  # Default mode for testing
                self.auto_analyze = req.auto_analyze
                self.auto_lora = req.auto_lora
                self.auto_prompt = req.auto_prompt
                self.internal_config = {
                    "stage1_prompt_analysis": {
                        "auto_analyze": req.auto_analyze,
                        "auto_lora": req.auto_lora,
                        "auto_prompt": req.auto_prompt,
                        "top_k_image_loras": req.top_k_image_loras,
                        "top_k_video_loras": req.top_k_video_loras
                    }
                }

        mock_req = MockRequest()
        result = await _analyze_prompt(mock_req, task_manager)

        if not result:
            return Stage1TestResponse(
                success=False,
                original_prompt=req.user_prompt,
                error="Analysis failed"
            )

        return Stage1TestResponse(
            success=True,
            original_prompt=req.user_prompt,
            optimized_t2i_prompt=result.get("optimized_t2i_prompt"),
            optimized_i2v_prompt=result.get("optimized_i2v_prompt"),
            image_loras=result.get("image_loras", []),
            video_loras=result.get("video_loras", [])
        )

    except Exception as e:
        logger.error(f"Stage 1 test failed: {e}", exc_info=True)
        return Stage1TestResponse(
            success=False,
            original_prompt=req.user_prompt,
            error=str(e)
        )


# ============================================================================
# Stage 2: First Frame Acquisition Test
# ============================================================================

class Stage2TestRequest(BaseModel):
    """Test Stage 2: First Frame Acquisition"""
    mode: str = Field(..., description="first_frame | face_reference | full_body_reference")
    resolution: str = Field(default="720p", description="480p | 720p | 1080p")
    aspect_ratio: str = Field(default="16:9", description="16:9 | 9:16 | 3:4 | 4:3")

    # Conditional inputs
    uploaded_first_frame: Optional[str] = Field(None, description="URL for first_frame mode")
    reference_image: Optional[str] = Field(None, description="URL for face/full_body modes")

    # Stage 2 config
    first_frame_source: str = Field(default="select_existing", description="generate | select_existing")
    t2i_prompt: Optional[str] = Field(None, description="Prompt for T2I generation")
    t2i_steps: int = Field(default=20, ge=10, le=50)
    t2i_cfg: float = Field(default=7.0, ge=1.0, le=20.0)
    t2i_sampler: str = Field(default="DPM++ 2M Karras")
    t2i_seed: int = Field(default=-1)

    # Face swap config
    face_swap_enabled: bool = Field(default=False)
    face_swap_strength: float = Field(default=1.0, ge=0.0, le=1.0)

    # Optional Stage 1 output
    image_loras: list = Field(default=[])


class Stage2TestResponse(BaseModel):
    """Stage 2 output"""
    success: bool
    first_frame_url: Optional[str] = None
    source: Optional[str] = None  # uploaded | generated | selected
    face_swapped: bool = False
    width: Optional[int] = None
    height: Optional[int] = None
    error: Optional[str] = None


@router.post("/stage2", response_model=Stage2TestResponse)
async def test_stage2_first_frame(
    req: Stage2TestRequest,
    task_manager: TaskManager = Depends(lambda: TaskManager())
):
    """
    Test Stage 2: First Frame Acquisition

    Entry criteria:
    - mode: valid enum value
    - resolution + aspect_ratio: valid combination
    - uploaded_first_frame: required if mode=first_frame
    - reference_image: required if mode=face_reference or full_body_reference

    Exit criteria:
    - first_frame_url: valid accessible URL
    - Image dimensions match resolution + aspect_ratio (±8px)
    - face_swapped: true if face_swap_enabled=true
    """
    try:
        # Validate inputs
        if req.mode == "first_frame" and not req.uploaded_first_frame:
            raise HTTPException(400, "uploaded_first_frame required for first_frame mode")

        if req.mode in ["face_reference", "full_body_reference"] and not req.reference_image:
            raise HTTPException(400, "reference_image required for face/full_body modes")

        # Create mock request
        class MockRequest:
            def __init__(self):
                self.mode = req.mode
                self.resolution = req.resolution
                self.aspect_ratio = req.aspect_ratio
                self.uploaded_first_frame = req.uploaded_first_frame
                self.reference_image = req.reference_image
                self.first_frame_source = req.first_frame_source
                self.user_prompt = req.t2i_prompt or "test prompt"
                self.internal_config = {
                    "stage2_first_frame": {
                        "first_frame_source": req.first_frame_source,
                        "t2i": {
                            "steps": req.t2i_steps,
                            "cfg_scale": req.t2i_cfg,
                            "sampler": req.t2i_sampler,
                            "seed": req.t2i_seed
                        },
                        "face_swap": {
                            "enabled": req.face_swap_enabled,
                            "strength": req.face_swap_strength
                        }
                    }
                }

        mock_req = MockRequest()

        # Mock analysis result for T2I
        analysis_result = {
            "optimized_t2i_prompt": req.t2i_prompt,
            "image_loras": req.image_loras
        } if req.t2i_prompt else None

        # Acquire first frame
        workflow_id = f"test_stage2_{id(req)}"
        first_frame_url = await _acquire_first_frame(
            workflow_id, mock_req, analysis_result, task_manager
        )

        if not first_frame_url:
            return Stage2TestResponse(
                success=False,
                error="Failed to acquire first frame"
            )

        # Determine source
        if req.mode == "first_frame":
            source = "uploaded"
        elif req.first_frame_source == "generate":
            source = "generated"
        else:
            source = "selected"

        # Check if face swap was applied
        face_swapped = False
        if req.face_swap_enabled and req.reference_image:
            face_swapped = True

        return Stage2TestResponse(
            success=True,
            first_frame_url=first_frame_url,
            source=source,
            face_swapped=face_swapped,
            width=None,  # TODO: extract from image
            height=None
        )

    except Exception as e:
        logger.error(f"Stage 2 test failed: {e}", exc_info=True)
        return Stage2TestResponse(
            success=False,
            error=str(e)
        )


# ============================================================================
# Stage 3: SeeDream Editing Test
# ============================================================================

class Stage3TestRequest(BaseModel):
    """Test Stage 3: SeeDream Editing"""
    mode: str = Field(..., description="first_frame | face_reference | full_body_reference")
    first_frame_url: str = Field(..., description="URL from Stage 2")
    reference_image: str = Field(..., description="Reference face/body image URL")

    # Stage 3 config
    enabled: bool = Field(default=True)
    edit_mode: str = Field(default="face_wearings", description="face_only | face_wearings | full_body")
    prompt: Optional[str] = Field(None, description="Custom SeeDream prompt")
    enable_reactor: bool = Field(default=True)
    strength: float = Field(default=0.8, ge=0.0, le=1.0)
    seed: Optional[int] = Field(None)


class Stage3TestResponse(BaseModel):
    """Stage 3 output"""
    success: bool
    edited_frame_url: Optional[str] = None
    mode: Optional[str] = None
    prompt_used: Optional[str] = None
    reactor_applied: bool = False
    skipped: bool = False
    error: Optional[str] = None


@router.post("/stage3", response_model=Stage3TestResponse)
async def test_stage3_seedream(
    req: Stage3TestRequest,
    task_manager: TaskManager = Depends(lambda: TaskManager())
):
    """
    Test Stage 3: SeeDream Editing

    Entry criteria:
    - first_frame_url: valid URL from Stage 2
    - reference_image: valid URL
    - enabled: true (unless mode=first_frame)

    Exit criteria:
    - edited_frame_url: valid accessible URL (if not skipped)
    - Image dimensions match input first_frame
    - reactor_applied: true if enable_reactor=true
    """
    try:
        # Check if should skip
        if req.mode == "first_frame" or not req.enabled:
            return Stage3TestResponse(
                success=True,
                edited_frame_url=req.first_frame_url,
                skipped=True
            )

        # TODO: Implement SeeDream API call
        # This requires the actual SeeDream service integration

        return Stage3TestResponse(
            success=False,
            error="SeeDream integration not yet implemented in test endpoint"
        )

    except Exception as e:
        logger.error(f"Stage 3 test failed: {e}", exc_info=True)
        return Stage3TestResponse(
            success=False,
            error=str(e)
        )


# ============================================================================
# Stage 4: Video Generation Test
# ============================================================================

class Stage4TestRequest(BaseModel):
    """Test Stage 4: Video Generation"""
    mode: str = Field(..., description="first_frame | face_reference | full_body_reference")
    first_frame_url: str = Field(..., description="URL from Stage 2 or Stage 3")
    prompt: str = Field(..., description="I2V prompt")
    duration: int = Field(..., description="5 | 10 | 15")
    resolution: str = Field(default="720p")
    aspect_ratio: str = Field(default="16:9")

    # Video generation config
    model: str = Field(default="A14B", description="A14B | 5B")
    steps: int = Field(default=20, ge=10, le=50)
    cfg: float = Field(default=6.0, ge=1.0, le=20.0)
    shift: float = Field(default=5.0, ge=0.0, le=10.0)
    scheduler: str = Field(default="unipc")
    noise_aug_strength: float = Field(default=0.05, ge=0.0, le=1.0)
    motion_amplitude: float = Field(default=0.0, ge=0.0, le=1.0)

    # Optional Stage 1 output
    video_loras: list = Field(default=[])

    # Postprocess config
    upscale_enabled: bool = Field(default=False)
    upscale_model: str = Field(default="RealESRGAN_x4plus")
    upscale_resize: float = Field(default=2.0)
    interp_enabled: bool = Field(default=False)
    interp_multiplier: int = Field(default=2)
    interp_profile: str = Field(default="auto")


class Stage4TestResponse(BaseModel):
    """Stage 4 output"""
    success: bool
    video_url: Optional[str] = None
    model: Optional[str] = None
    duration: Optional[int] = None
    upscaled: bool = False
    interpolated: bool = False
    error: Optional[str] = None


@router.post("/stage4", response_model=Stage4TestResponse)
async def test_stage4_video_generation(
    req: Stage4TestRequest,
    task_manager: TaskManager = Depends(lambda: TaskManager())
):
    """
    Test Stage 4: Video Generation

    Entry criteria:
    - first_frame_url: valid URL
    - prompt: non-empty string
    - duration: valid value (5, 10, 15)

    Exit criteria:
    - video_url: valid accessible URL
    - Video duration matches requested duration (±0.5s)
    - Video resolution matches requested resolution
    - Video is playable MP4
    """
    try:
        # TODO: Implement Chain video generation call
        # This requires integration with the existing Chain workflow

        return Stage4TestResponse(
            success=False,
            error="Video generation integration not yet implemented in test endpoint"
        )

    except Exception as e:
        logger.error(f"Stage 4 test failed: {e}", exc_info=True)
        return Stage4TestResponse(
            success=False,
            error=str(e)
        )
