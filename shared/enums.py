"""Shared enumerations used by both API gateway and GPU worker."""

from enum import Enum


class ModelType(str, Enum):
    A14B = "a14b"
    FIVE_B = "5b"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class GenerateMode(str, Enum):
    T2V = "t2v"
    I2V = "i2v"
    EXTEND = "extend"
    VACE_REF2V = "vace_ref2v"
    VACE_V2V = "vace_v2v"
    VACE_INPAINTING = "vace_inpainting"
    VACE_FLF2V = "vace_flf2v"
    CONCAT = "concat"  # ffmpeg concat of multiple video segments
    INTERPOLATE = "interpolate"  # RIFE frame interpolation
    UPSCALE = "upscale"  # video upscaling
    AUDIO = "audio"  # MMAudio generation
    FACESWAP = "faceswap"  # ReActor face swap
    LORA_DOWNLOAD = "lora_download"  # download LoRA from CivitAI
    # Third-party generation modes
    WAN26_T2V = "wan26_t2v"
    WAN26_I2V = "wan26_i2v"
    SEEDANCE_T2V = "seedance_t2v"
    SEEDANCE_I2V = "seedance_i2v"
    CLOTHOFF = "clothoff"
    # Inference modes (handled by gpu/inference_worker, not gpu/comfyui_worker)
    INFERENCE_EMBED = "inference_embed"          # BGE text embedding
    INFERENCE_DESCRIBE = "inference_describe"    # VLM image description
    INFERENCE_CHAT = "inference_chat"            # LLM chat completion / rerank


class TaskCategory(str, Enum):
    """Classification of tasks by execution context."""
    LOCAL = "local"           # Executed on local ComfyUI GPU workers
    THIRDPARTY = "thirdparty" # Delegated to third-party APIs
    POSTPROCESS = "postprocess"  # Post-processing (concat, interpolate, upscale, audio)
    UTILITY = "utility"       # Utility tasks (lora_download, etc.)
    INFERENCE = "inference"   # LLM/VLM/embedding via gpu/inference_worker


# Mapping from GenerateMode to TaskCategory
_MODE_CATEGORY_MAP: dict[GenerateMode, TaskCategory] = {
    # Local ComfyUI modes
    GenerateMode.T2V: TaskCategory.LOCAL,
    GenerateMode.I2V: TaskCategory.LOCAL,
    GenerateMode.EXTEND: TaskCategory.LOCAL,
    GenerateMode.VACE_REF2V: TaskCategory.LOCAL,
    GenerateMode.VACE_V2V: TaskCategory.LOCAL,
    GenerateMode.VACE_INPAINTING: TaskCategory.LOCAL,
    GenerateMode.VACE_FLF2V: TaskCategory.LOCAL,
    GenerateMode.FACESWAP: TaskCategory.LOCAL,
    # Third-party API modes
    GenerateMode.WAN26_T2V: TaskCategory.THIRDPARTY,
    GenerateMode.WAN26_I2V: TaskCategory.THIRDPARTY,
    GenerateMode.SEEDANCE_T2V: TaskCategory.THIRDPARTY,
    GenerateMode.SEEDANCE_I2V: TaskCategory.THIRDPARTY,
    GenerateMode.CLOTHOFF: TaskCategory.THIRDPARTY,
    # Post-processing modes
    GenerateMode.CONCAT: TaskCategory.POSTPROCESS,
    GenerateMode.INTERPOLATE: TaskCategory.POSTPROCESS,
    GenerateMode.UPSCALE: TaskCategory.POSTPROCESS,
    GenerateMode.AUDIO: TaskCategory.POSTPROCESS,
    # Utility modes
    GenerateMode.LORA_DOWNLOAD: TaskCategory.UTILITY,
    # Inference modes
    GenerateMode.INFERENCE_EMBED: TaskCategory.INFERENCE,
    GenerateMode.INFERENCE_DESCRIBE: TaskCategory.INFERENCE,
    GenerateMode.INFERENCE_CHAT: TaskCategory.INFERENCE,
}


def category_for_mode(mode: GenerateMode) -> TaskCategory:
    """Return the TaskCategory for a given GenerateMode.

    Falls back to LOCAL for any unmapped mode.
    """
    return _MODE_CATEGORY_MAP.get(mode, TaskCategory.LOCAL)
