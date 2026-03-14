from typing import Optional, Literal
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from .enums import ModelType, TaskStatus, GenerateMode

VALID_SCHEDULERS = [
    "unipc", "unipc/beta",
    "dpm++", "dpm++/beta",
    "dpm++_sde", "dpm++_sde/beta",
    "euler", "euler/beta",
    "longcat_distill_euler",
    "deis",
    "lcm", "lcm/beta",
    "res_multistep",
    "er_sde",
    "flowmatch_causvid",
    "flowmatch_distill",
    "flowmatch_pusa",
    "multitalk",
    "sa_ode_stable",
    "rcm",
    "vibt_unipc",
]


class ImageMode(str, Enum):
    """Image upload mode for chain generation."""
    FIRST_FRAME = "first_frame"
    FACE_REFERENCE = "face_reference"
    FULL_BODY_REFERENCE = "full_body_reference"


class LoraInput(BaseModel):
    name: str
    strength: float = Field(default=0.8, ge=-2.0, le=2.0)


class FaceSwapConfig(BaseModel):
    """Face swap configuration for Reactor"""
    enabled: bool = Field(default=False)
    strength: float = Field(default=0.8, ge=0.3, le=1.0, description="Face swap strength, 0.3=light, 0.8=recommended, 1.0=full")


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    negative_prompt: str = Field(default="", max_length=2000)
    model: ModelType = ModelType.A14B
    model_preset: str = Field(default="", description="Model preset name, e.g. 'default', 'nsfw_v2'")
    width: int = Field(default=848, ge=64, le=1920, multiple_of=8)
    height: int = Field(default=480, ge=64, le=1920, multiple_of=8)
    num_frames: int = Field(default=81, ge=1, le=241)
    fps: int = Field(default=16, ge=1, le=60, description="Frames per second (default: 16 for optimal performance)")
    steps: int = Field(default=20, ge=1, le=100)
    cfg: float = Field(default=6.0, ge=0.0, le=30.0)
    shift: float = Field(default=5.0, ge=0.0, le=20.0)
    seed: Optional[int] = Field(default=None, ge=0)
    loras: list[LoraInput] = Field(default_factory=list)
    auto_lora: bool = Field(default=False, description="Auto-select LoRAs based on prompt")
    auto_prompt: bool = Field(default=False, description="Auto-optimize prompt before generation")
    scheduler: str = Field(default="unipc")
    upscale: bool = Field(default=False, description="Enable 2x upscaling after generation")
    t5_preset: str = Field(default="", description="T5 text encoder preset, e.g. 'default', 'nsfw'")
    face_swap: Optional[FaceSwapConfig] = Field(default=None, description="Face swap configuration using Reactor")

    @field_validator("scheduler")
    @classmethod
    def validate_scheduler(cls, v):
        if v not in VALID_SCHEDULERS:
            raise ValueError(f"Invalid scheduler '{v}'. Valid: {VALID_SCHEDULERS}")
        return v


class GenerateI2VRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    negative_prompt: str = Field(default="", max_length=2000)
    model: ModelType = ModelType.A14B
    model_preset: str = Field(default="", description="Model preset name")
    width: int = Field(default=832, ge=64, le=1920, multiple_of=8)
    height: int = Field(default=480, ge=64, le=1920, multiple_of=8)
    num_frames: int = Field(default=81, ge=1, le=241)
    fps: int = Field(default=16, ge=1, le=60, description="Frames per second (default: 16 for optimal performance)")
    steps: int = Field(default=20, ge=1, le=100)
    cfg: float = Field(default=6.0, ge=0.0, le=30.0)
    shift: float = Field(default=5.0, ge=0.0, le=20.0)
    seed: Optional[int] = Field(default=None, ge=0)
    loras: list[LoraInput] = Field(default_factory=list)
    auto_lora: bool = Field(default=False, description="Auto-select LoRAs based on prompt")
    auto_prompt: bool = Field(default=False, description="Auto-optimize prompt before generation")
    scheduler: str = Field(default="unipc")
    noise_aug_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    motion_amplitude: float = Field(default=0.0, ge=0.0, le=2.0, description="Augment empty frames strength (0=disabled, 0.15=recommended for I2V, 1.15=recommended for Story)")
    color_match: bool = Field(default=True, description="Enable ColorMatch post-processing")
    color_match_method: str = Field(default="mkl", description="ColorMatch method: mkl/hm/reinhard")
    resize_mode: str = Field(default="crop_to_new", description="Image resize mode: crop_to_new/stretch_to_new/keep_input")
    upscale: bool = Field(default=False, description="Enable 2x upscaling after generation")
    t5_preset: str = Field(default="", description="T5 text encoder preset, e.g. 'default', 'nsfw'")
    face_swap: Optional[FaceSwapConfig] = Field(default=None, description="Face swap configuration using Reactor")

    @field_validator("scheduler")
    @classmethod
    def validate_scheduler(cls, v):
        if v not in VALID_SCHEDULERS:
            raise ValueError(f"Invalid scheduler '{v}'. Valid: {VALID_SCHEDULERS}")
        return v

    @field_validator("color_match_method")
    @classmethod
    def validate_color_match_method(cls, v):
        valid = ["mkl", "hm", "reinhard"]
        if v not in valid:
            raise ValueError(f"Invalid color_match_method '{v}'. Valid: {valid}")
        return v

    @field_validator("resize_mode")
    @classmethod
    def validate_resize_mode(cls, v):
        valid = ["crop_to_new", "stretch_to_new", "keep_input"]
        if v not in valid:
            raise ValueError(f"Invalid resize_mode '{v}'. Valid: {valid}")
        return v


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    mode: Optional[str] = None
    model: Optional[str] = None
    progress: Optional[float] = None
    video_url: Optional[str] = None
    last_frame_url: Optional[str] = None
    error: Optional[str] = None
    params: Optional[dict] = None
    created_at: Optional[int] = None
    completed_at: Optional[int] = None


class GenerateResponse(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.QUEUED


class LoraInfo(BaseModel):
    name: str
    file: str
    description: str = ""
    default_strength: float = 0.8
    trigger_words: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    civitai_id: Optional[int] = None
    civitai_version_id: Optional[int] = None
    preview_url: Optional[str] = None


class CivitAIFile(BaseModel):
    name: str
    size_mb: float = 0
    download_url: str = ""


class CivitAIModelVersion(BaseModel):
    id: int
    name: str
    trained_words: list[str] = Field(default_factory=list)
    download_url: str = ""
    base_model: str = ""
    file_size_mb: float = 0
    files: list[CivitAIFile] = Field(default_factory=list)


class CivitAIModelResult(BaseModel):
    id: int
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    preview_url: Optional[str] = None
    versions: list[CivitAIModelVersion] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


class CivitAISearchResponse(BaseModel):
    items: list[CivitAIModelResult]
    next_cursor: str = ""


class CivitAIDownloadRequest(BaseModel):
    model_id: int
    version_id: int = 0
    filename: str = ""
    download_url: str = ""  # direct file URL, overrides version_id


class PromptOptimizeRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    lora_names: list[str] = Field(default_factory=list)
    mode: str = Field(default="i2v", pattern="^(t2v|i2v)$")
    image_base64: Optional[str] = Field(default=None, description="Base64 encoded image for I2V mode")
    duration: float = Field(default=3.3, ge=0.5, le=10, description="Video duration in seconds")


class PromptOptimizeResponse(BaseModel):
    original_prompt: str
    optimized_prompt: str
    trigger_words_used: list[str] = Field(default_factory=list)
    explanation: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    comfyui_a14b: bool = False
    comfyui_5b: bool = False
    redis: bool = False


class LoraRecommendRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)


class LoraRecommendResponse(BaseModel):
    loras: list[LoraInput] = Field(default_factory=list)


class ExtendRequest(BaseModel):
    parent_task_id: str
    prompt: str = Field(..., min_length=1, max_length=2000)
    negative_prompt: str = Field(default="", max_length=2000)
    num_frames: Optional[int] = Field(default=None, ge=1, le=241)
    steps: Optional[int] = Field(default=None, ge=1, le=100)
    cfg: Optional[float] = Field(default=None, ge=0.0, le=30.0)
    shift: Optional[float] = Field(default=None, ge=0.0, le=20.0)
    seed: Optional[int] = Field(default=None, ge=0)
    scheduler: Optional[str] = None
    noise_aug_strength: float = Field(default=0.05, ge=0.0, le=1.0)
    loras: Optional[list[LoraInput]] = None
    auto_prompt: bool = False
    concat_with_parent: bool = True

    @field_validator("scheduler")
    @classmethod
    def validate_scheduler(cls, v):
        if v is not None and v not in VALID_SCHEDULERS:
            raise ValueError(f"Invalid scheduler '{v}'. Valid: {VALID_SCHEDULERS}")
        return v


class ChainSegment(BaseModel):
    """Single segment in a chain generation."""
    prompt: str = Field(..., min_length=1, max_length=2000)
    duration: float = Field(default=3.3, ge=0.5, le=10.0)
    loras: list[LoraInput] = Field(default_factory=list)


class AutoChainRequest(BaseModel):
    # New format: segments array (takes priority if provided)
    segments: Optional[list[ChainSegment]] = None

    # Legacy format: single prompt + total_duration/segment_duration
    prompt: Optional[str] = Field(default=None, max_length=2000)
    total_duration: Optional[float] = Field(default=None, ge=4.0, le=120.0)
    segment_duration: Optional[float] = Field(default=3.3, ge=1.0, le=10.0)

    # Story Mode continuation: reference existing video for consistency
    parent_chain_id: Optional[str] = Field(default=None, description="Parent chain ID to continue from (for Story Mode)")
    parent_video_url: Optional[str] = Field(default=None, description="Parent video URL to extract last frame from")
    initial_reference_url: Optional[str] = Field(default=None, description="Initial reference image URL for identity consistency")

    # Image mode: how to use uploaded image
    image_mode: ImageMode = Field(default=ImageMode.FIRST_FRAME, description="Image usage mode: first_frame (I2V) or face_reference (T2V + Reactor)")
    face_swap_strength: float = Field(default=1.0, ge=0.3, le=1.0, description="Face swap strength when using face_reference mode")

    # Shared parameters
    negative_prompt: str = Field(default="", max_length=2000)
    model: ModelType = ModelType.A14B
    model_preset: str = ""
    width: int = Field(default=832, ge=64, le=1920, multiple_of=8)
    height: int = Field(default=480, ge=64, le=1920, multiple_of=8)
    fps: int = Field(default=16, ge=1, le=60, description="Frames per second (default: 16 for optimal performance)")
    steps: int = Field(default=20, ge=1, le=100)
    cfg: float = Field(default=6.0, ge=0.0, le=30.0)
    shift: float = Field(default=5.0, ge=0.0, le=20.0)
    seed: Optional[int] = Field(default=None, ge=0)
    loras: list[LoraInput] = Field(default_factory=list)
    auto_lora: bool = False
    auto_prompt: bool = False
    scheduler: str = Field(default="unipc")
    noise_aug_strength: float = Field(default=0.05, ge=0.0, le=1.0)
    motion_amplitude: float = Field(default=1.15, ge=0.0, le=2.0, description="Motion amplitude (0=disabled, 1.15=recommended)")
    color_match: bool = True
    color_match_method: str = Field(default="mkl")
    resize_mode: str = Field(default="crop_to_new")
    upscale: bool = False
    transition: str = Field(default="none", description="Transition between segments: none/crossfade")
    auto_continue: bool = Field(default=True, description="Use VLM to auto-generate continuation prompts")
    t5_preset: str = Field(default="", description="T5 text encoder preset, e.g. 'default', 'nsfw'")
    motion_frames: int = Field(default=5, ge=1, le=73, description="Motion reference frames for story mode")
    boundary: float = Field(default=0.9, ge=0.0, le=1.0, description="Boundary for WanMoeKSampler in story mode")
    clip_preset: str = Field(default="", description="CLIP preset for story mode (e.g. 'nsfw', 'default')")
    match_image_ratio: bool = Field(default=False, description="Auto-adjust resolution to match input image aspect ratio (ignores width/height)")

    # Post-processing: Upscale
    enable_upscale: bool = Field(default=False, description="Enable TensorRT upscale")
    upscale_model: str = Field(default="4x-UltraSharp", description="Upscale model name")
    upscale_resize: str = Field(default="2x", description="Upscale resize target: 1.5x/2x/3x/4x")

    # Post-processing: Frame interpolation
    enable_interpolation: bool = Field(default=False, description="Enable RIFE TensorRT frame interpolation")
    interpolation_multiplier: int = Field(default=2, ge=2, le=4, description="Frame rate multiplier")
    interpolation_profile: str = Field(default="small", description="RIFE TRT profile: small/large")

    # Post-processing: MMAudio
    enable_mmaudio: bool = Field(default=False, description="Enable MMAudio video-to-audio")
    mmaudio_prompt: str = Field(default="", max_length=500, description="Audio generation prompt")
    mmaudio_negative_prompt: str = Field(default="", max_length=500, description="Audio negative prompt")
    mmaudio_steps: int = Field(default=25, ge=1, le=100, description="MMAudio sampling steps")
    mmaudio_cfg: float = Field(default=4.5, ge=0.0, le=20.0, description="MMAudio CFG scale")

    @field_validator("transition")
    @classmethod
    def validate_transition(cls, v):
        valid = ["none", "crossfade"]
        if v not in valid:
            raise ValueError(f"Invalid transition '{v}'. Valid: {valid}")
        return v

    @field_validator("scheduler")
    @classmethod
    def validate_scheduler(cls, v):
        if v not in VALID_SCHEDULERS:
            raise ValueError(f"Invalid scheduler '{v}'. Valid: {VALID_SCHEDULERS}")
        return v


class ChainResponse(BaseModel):
    chain_id: str
    total_segments: int
    completed_segments: int = 0
    current_segment: int = 0
    current_task_id: Optional[str] = None
    current_task_progress: float = 0.0
    segment_task_ids: list[str] = Field(default_factory=list)
    status: str = "queued"
    final_video_url: Optional[str] = None
    error: Optional[str] = None
    params: Optional[dict] = None
    created_at: Optional[int] = None
    completed_at: Optional[int] = None
