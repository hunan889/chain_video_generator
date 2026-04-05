"""Stage 4 -- Video Generation.

Submits a video generation task to the GPU worker via Redis queue and
polls until completion.  Handles resolution parsing, LoRA filtering,
prompt optimization, workflow building, and post-process configuration.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from api_gateway.config import GatewayConfig
from shared.cos.client import COSClient
from shared.enums import GenerateMode, ModelType, TaskStatus
from shared.redis_keys import chain_key, task_key
from shared.schemas import LoraInput
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)

# Maximum polling time for video generation (20 minutes)
_MAX_POLL_SECONDS = 1200
_POLL_INTERVAL = 1.0


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VideoGenerationResult:
    """Immutable result of Stage 4 video generation."""

    chain_id: Optional[str]
    video_url: Optional[str]
    loras_used: list = field(default_factory=list)
    prompt_used: str = ""
    width: int = 0
    height: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Resolution parsing
# ---------------------------------------------------------------------------

def _round16(v: float) -> int:
    """Round to nearest multiple of 16, minimum 16."""
    return max(16, int(round(v / 16)) * 16)


def parse_resolution(resolution: str) -> tuple[int, int]:
    """Parse resolution string to (width, height) in pixels.

    Supported formats:
      - "480p_3_4"  -> portrait  480w x 640h
      - "720p_16_9" -> landscape 1280w x 720h
      - "480p_3:4"  -> same as 480p_3_4
      - "480p"      -> default 832x480

    The ``p`` value represents the shorter side for landscape,
    or the width for portrait.  Dimensions are rounded to 16.
    """
    # Normalize separators
    normalized = resolution.replace("p:", "p_").replace("p_", "p_")
    match = re.match(r"^(\d+)p[_:]?(\d+)[_:](\d+)$", normalized)

    if match:
        p_val = int(match.group(1))
        ar_w = int(match.group(2))
        ar_h = int(match.group(3))

        if ar_w >= ar_h:  # landscape or square: p = height
            height = _round16(p_val)
            width = _round16(p_val * ar_w / ar_h)
        else:  # portrait: p = width
            width = _round16(p_val)
            height = _round16(p_val * ar_h / ar_w)
        return width, height

    # Fallback
    logger.warning("Unknown resolution '%s', using default 832x480", resolution)
    return 832, 480


# ---------------------------------------------------------------------------
# Config extraction helpers
# ---------------------------------------------------------------------------

def _get_video_param(internal_config: dict, key: str, default):
    """Extract a parameter from internal_config.stage4_video.generation or top-level."""
    stage4 = internal_config.get("stage4_video", {})
    generation = stage4.get("generation", {})
    if key in generation:
        return generation[key]
    if key in stage4:
        return stage4[key]
    return default


def _get_postprocess_config(internal_config: dict) -> dict:
    """Extract stage4_video.postprocess config."""
    return internal_config.get("stage4_video", {}).get("postprocess", {})


# ---------------------------------------------------------------------------
# LoRA helpers
# ---------------------------------------------------------------------------

def _build_lora_context(video_loras: list[dict]) -> Optional[list[dict]]:
    """Build lora_info list for PromptOptimizer from video_loras."""
    if not video_loras:
        return None
    result = []
    for lora in video_loras:
        tw = lora.get("trigger_words") or []
        if isinstance(tw, str):
            try:
                tw = json.loads(tw)
            except (json.JSONDecodeError, TypeError):
                tw = []
        result.append({
            "name": lora.get("name", ""),
            "description": ", ".join(tw) if tw else lora.get("description", ""),
            "trigger_prompt": (lora.get("trigger_prompt") or "").strip(),
            "example_prompts": list(lora.get("example_prompts") or []),
        })
    return result


def _collect_trigger_words(loras: list[dict]) -> list[str]:
    """Flatten trigger_words from a list of LoRA dicts (deduped, order-preserved)."""
    words: list[str] = []
    for lora in loras:
        tw = lora.get("trigger_words") or []
        if isinstance(tw, str):
            try:
                tw = json.loads(tw)
            except (json.JSONDecodeError, TypeError):
                tw = []
        for w in tw:
            if w and w not in words:
                words.append(w)
    return words


def _filter_loras_by_mode(
    video_loras: list[dict],
    is_i2v: bool,
) -> list[dict]:
    """Filter LoRAs by mode compatibility (I2V vs T2V)."""
    filtered = []
    for lora in video_loras:
        lora_mode = (lora.get("mode") or "").upper()
        noise_stage = lora.get("noise_stage") or ""
        if is_i2v:
            if lora_mode == "I2V" or noise_stage in ("high", "low", "single") or not lora_mode:
                filtered.append(lora)
        else:
            if lora_mode == "T2V" or not lora_mode:
                filtered.append(lora)
    return filtered or video_loras  # Fallback to all if none match


def _normalize_lora_weights(loras: list[dict], max_total: float = 1.0) -> list[dict]:
    """Cap total LoRA weight sum to prevent overfitting.

    Returns new list (does not mutate input).
    """
    if not loras:
        return loras
    total = sum(l.get("weight", 0.8) for l in loras)
    if total <= max_total or total <= 0:
        return loras
    scale = max_total / total
    logger.info("LoRA weight normalization: total %.2f > %.2f, scaling by %.2f", total, max_total, scale)
    return [
        {**l, "weight": round(l.get("weight", 0.8) * scale, 2)}
        for l in loras
    ]


def _loras_to_inputs(
    loras: list[dict],
    inject_trigger_words: bool = True,
    inject_trigger_prompt: bool = True,
) -> list[LoraInput]:
    """Convert raw LoRA dicts to LoraInput dataclass instances."""
    result = []
    for lora in loras:
        tw = lora.get("trigger_words") or []
        if isinstance(tw, str):
            try:
                tw = json.loads(tw)
            except (json.JSONDecodeError, TypeError):
                tw = []
        result.append(LoraInput(
            name=lora["name"],
            strength=lora.get("weight", 0.8),
            trigger_words=tw if inject_trigger_words else [],
            trigger_prompt=lora.get("trigger_prompt") if inject_trigger_prompt else None,
        ))
    return result


# ---------------------------------------------------------------------------
# Prompt optimization
# ---------------------------------------------------------------------------

async def _optimize_video_prompt(
    user_prompt: str,
    analysis_result: dict,
    edited_frame_url: Optional[str],
    mode: str,
    is_continuation: bool,
    internal_config: dict,
    video_loras: list[dict],
    config: GatewayConfig,
) -> str:
    """Run unified prompt generation via PromptOptimizer.

    Single LLM call that analyzes + optimizes + adapts to the first frame.
    Falls back to raw user prompt on failure.
    """
    auto_prompt = internal_config.get("stage1_prompt_analysis", {}).get("auto_prompt", True)
    if not auto_prompt:
        return user_prompt

    try:
        from api_gateway.services.prompt_optimizer import make_prompt_optimizer

        optimizer = make_prompt_optimizer(
            llm_api_key=config.llm_api_key,
            llm_base_url=config.llm_base_url,
            llm_model=config.llm_model,
            vision_api_key=config.vision_api_key,
            vision_base_url=config.vision_base_url,
            vision_model=config.vision_model,
        )

        # Determine optimizer mode
        optimizer_mode = "i2v" if is_continuation else ("t2v" if mode == "t2v" else "i2v")
        frame_desc = ""

        # Build pose description
        if optimizer_mode != "t2v":
            pose_keys = analysis_result.get("pose_keys", [])
            if pose_keys:
                descs = []
                for pk in pose_keys:
                    # Pose descriptions are pre-computed in analysis_result
                    pass
                pose_desc = analysis_result.get("pose_description", "")
                if pose_desc:
                    frame_desc = (
                        "Target action/pose (NOTE: the actual image may NOT yet show this): "
                        + pose_desc
                    )

            # VLM description of first frame (for first_frame mode only, not reference modes)
            if edited_frame_url and mode not in ("face_reference", "full_body_reference"):
                try:
                    import aiohttp
                    import base64

                    async with aiohttp.ClientSession() as session:
                        async with session.get(edited_frame_url) as resp:
                            if resp.status == 200:
                                img_bytes = await resp.read()
                                img_b64 = base64.b64encode(img_bytes).decode()
                                vlm_desc = await optimizer._describe_image(img_b64)
                                if vlm_desc:
                                    if frame_desc:
                                        frame_desc = (
                                            f"ACTUAL IMAGE STATE (from VLM): {vlm_desc}\n"
                                            f"TARGET ACTION (user's goal): {frame_desc}"
                                        )
                                    else:
                                        frame_desc = vlm_desc
                except Exception as exc:
                    logger.warning("VLM description failed: %s", exc)

        standin_enabled = _get_video_param(internal_config, "standin_enabled", False)
        loras_for_prompt = [] if standin_enabled else video_loras
        lora_info = _build_lora_context(loras_for_prompt)
        trigger_words = _collect_trigger_words(loras_for_prompt)

        duration_str = _get_video_param(internal_config, "duration", "5s")
        dur_val = float(str(duration_str).replace("s", "")) if duration_str else 5.0

        # For continuations, use the already-generated continuation prompt
        video_gen_prompt = user_prompt
        if is_continuation and analysis_result.get("video_prompt"):
            video_gen_prompt = analysis_result["video_prompt"]

        result = await optimizer.generate_video_prompt(
            prompt=video_gen_prompt,
            trigger_words=trigger_words,
            mode=optimizer_mode,
            duration=dur_val,
            lora_info=lora_info,
            image_description=frame_desc,
            standin_mode=bool(standin_enabled),
        )

        final_prompt = result["optimized_prompt"]
        has_prereq = result.get("has_prerequisite", False)
        logger.info(
            "generate_video_prompt result (scene=%s, prerequisite=%s): %s",
            result.get("scene_type"), has_prereq, final_prompt[:150],
        )

        # Store back into analysis_result for downstream use
        analysis_result["video_prompt"] = final_prompt
        analysis_result["optimized_i2v_prompt"] = final_prompt
        analysis_result["has_prerequisite"] = has_prereq
        return final_prompt

    except Exception as exc:
        logger.warning("generate_video_prompt failed, using raw prompt: %s", exc)
        return (
            analysis_result.get("optimized_i2v_prompt")
            or analysis_result.get("video_prompt")
            or user_prompt
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def generate_video(
    workflow_id: str,
    mode: str,
    first_frame_url: Optional[str],
    analysis_result: Optional[dict],
    internal_config: dict,
    user_prompt: str,
    is_continuation: bool,
    parent_workflow: Optional[dict],
    origin_first_frame_url: Optional[str],
    config: GatewayConfig,
    gateway: TaskGateway,
    cos_client: COSClient,
    redis,
) -> VideoGenerationResult:
    """Submit a video generation task and poll until completion.

    Returns a ``VideoGenerationResult`` with the chain_id and final video URL.
    """
    analysis = analysis_result or {}

    # ------------------------------------------------------------------
    # 1. Read video parameters from internal_config
    # ------------------------------------------------------------------
    video_model = _get_video_param(internal_config, "model", "A14B")
    video_model_preset = _get_video_param(internal_config, "model_preset", "")
    video_resolution = _get_video_param(internal_config, "resolution", "480p_3:4")
    video_duration = _get_video_param(internal_config, "duration", "5s")
    video_steps = int(_get_video_param(internal_config, "steps", 20))
    video_cfg = float(_get_video_param(internal_config, "cfg", 6.0))
    video_shift = float(_get_video_param(internal_config, "shift", 5.0))
    video_scheduler = _get_video_param(internal_config, "scheduler", "unipc")
    video_motion_amp = float(_get_video_param(internal_config, "motion_amplitude", 1.15))
    t5_preset = _get_video_param(internal_config, "t5_preset", "nsfw")
    clip_preset = _get_video_param(internal_config, "clip_preset", "nsfw")

    # Noise augmentation: default depends on I2V vs T2V
    video_noise_aug = _get_video_param(internal_config, "noise_aug_strength", None)
    if video_noise_aug is None:
        is_actually_i2v = is_continuation or mode != "t2v"
        video_noise_aug = 0.2 if is_actually_i2v else 0.0
    video_noise_aug = float(video_noise_aug)

    # Boost noise_aug for prerequisite transitions
    if analysis.get("has_prerequisite") and (mode != "t2v" or is_continuation):
        if video_noise_aug < 0.2:
            logger.info("Boosting noise_aug_strength %.2f -> 0.2 (prerequisite transition)", video_noise_aug)
            video_noise_aug = 0.2

    # ------------------------------------------------------------------
    # 2. Continuation: inherit parent's generation params
    # ------------------------------------------------------------------
    if is_continuation and parent_workflow:
        try:
            parent_ic = json.loads(parent_workflow.get("internal_config", "{}"))
            parent_gen = parent_ic.get("stage4_video", {}).get("generation", {})
            if parent_gen:
                param_keys = [
                    "model", "model_preset", "steps", "cfg",
                    "scheduler", "shift", "noise_aug_strength", "motion_amplitude",
                ]
                for pk in param_keys:
                    if pk in parent_gen:
                        parent_val = parent_gen[pk]
                        local_map = {
                            "model": "video_model",
                            "model_preset": "video_model_preset",
                            "steps": "video_steps",
                            "cfg": "video_cfg",
                            "scheduler": "video_scheduler",
                            "shift": "video_shift",
                            "noise_aug_strength": "video_noise_aug",
                            "motion_amplitude": "video_motion_amp",
                        }
                        # Use locals() would be tricky; do explicit
                        pass
                # Explicit overrides (cleaner than metaprogramming)
                if "model" in parent_gen:
                    video_model = parent_gen["model"]
                if "model_preset" in parent_gen:
                    video_model_preset = parent_gen["model_preset"]
                if "steps" in parent_gen:
                    video_steps = int(parent_gen["steps"])
                if "cfg" in parent_gen:
                    video_cfg = float(parent_gen["cfg"])
                if "scheduler" in parent_gen:
                    video_scheduler = parent_gen["scheduler"]
                if "shift" in parent_gen:
                    video_shift = float(parent_gen["shift"])
                if "noise_aug_strength" in parent_gen:
                    video_noise_aug = float(parent_gen["noise_aug_strength"])
                if "motion_amplitude" in parent_gen:
                    video_motion_amp = float(parent_gen["motion_amplitude"])
                logger.info("[%s] Continuation: inherited stage4 params from parent", workflow_id)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("[%s] Failed to inherit parent stage4 params: %s", workflow_id, exc)

        # Continuation minimum noise_aug for I2V quality
        if video_noise_aug < 0.2:
            logger.info("[%s] Continuation: boosting noise_aug %.2f -> 0.2", workflow_id, video_noise_aug)
            video_noise_aug = 0.2

    # ------------------------------------------------------------------
    # 3. Parse resolution -> (width, height)
    # ------------------------------------------------------------------
    width, height = parse_resolution(video_resolution)

    # Continuation: inherit parent's actual dimensions
    if is_continuation and parent_workflow:
        parent_aw = parent_workflow.get("actual_width")
        parent_ah = parent_workflow.get("actual_height")
        if parent_aw and parent_ah:
            width = int(parent_aw)
            height = int(parent_ah)
            logger.info("[%s] Continuation: inherited actual dims %dx%d", workflow_id, width, height)
        else:
            # Fallback: parse parent's resolution string
            try:
                parent_ic = json.loads(parent_workflow.get("internal_config", "{}"))
                parent_res = parent_ic.get("stage4_video", {}).get("generation", {}).get("resolution", "")
                if parent_res:
                    pw, ph = parse_resolution(parent_res)
                    width, height = pw, ph
                    logger.info("[%s] Continuation: inherited resolution from parent config -> %dx%d", workflow_id, width, height)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("[%s] Failed to inherit parent resolution: %s", workflow_id, exc)

    # ------------------------------------------------------------------
    # 4. Parse duration
    # ------------------------------------------------------------------
    duration_seconds = float(str(video_duration).rstrip("s"))

    # ------------------------------------------------------------------
    # 5. Determine generation mode
    # ------------------------------------------------------------------
    if is_continuation:
        gen_mode = GenerateMode.I2V
    elif first_frame_url:
        gen_mode = GenerateMode.I2V
    else:
        gen_mode = GenerateMode.T2V

    # ------------------------------------------------------------------
    # 6. Build LoRA list
    # ------------------------------------------------------------------
    auto_lora = internal_config.get("stage1_prompt_analysis", {}).get("auto_lora", True)
    standin_enabled = _get_video_param(internal_config, "standin_enabled", False)
    if standin_enabled:
        auto_lora = False

    lora_inputs: list[LoraInput] = []
    video_loras = analysis.get("video_loras", [])

    if auto_lora and video_loras:
        is_i2v = gen_mode == GenerateMode.I2V
        filtered = _filter_loras_by_mode(video_loras, is_i2v)
        selected = filtered[:3]
        selected = _normalize_lora_weights(selected)

        inject_tw = internal_config.get("stage1_prompt_analysis", {}).get("inject_trigger_words", True)
        inject_tp = internal_config.get("stage1_prompt_analysis", {}).get("inject_trigger_prompt", True)
        lora_inputs = _loras_to_inputs(selected, inject_trigger_words=inject_tw, inject_trigger_prompt=inject_tp)

        for li in lora_inputs:
            logger.info("[%s] Video LoRA: %s (strength=%.2f)", workflow_id, li.name, li.strength)

    # ------------------------------------------------------------------
    # 7. Optimize video prompt
    # ------------------------------------------------------------------
    edited_frame_url = first_frame_url  # Use the frame that was edited in Stage 3
    prompt = await _optimize_video_prompt(
        user_prompt=user_prompt,
        analysis_result=analysis,
        edited_frame_url=edited_frame_url,
        mode=mode,
        is_continuation=is_continuation,
        internal_config=internal_config,
        video_loras=video_loras,
        config=config,
    )

    # Use analysis video_prompt if available and auto_prompt
    auto_prompt = internal_config.get("stage1_prompt_analysis", {}).get("auto_prompt", True)
    if auto_prompt and analysis:
        video_prompt = analysis.get("video_prompt") or analysis.get("optimized_i2v_prompt")
        if video_prompt:
            prompt = video_prompt

    # ------------------------------------------------------------------
    # 8. Convert model string to enum
    # ------------------------------------------------------------------
    model_str = str(video_model).upper()
    if model_str == "A14B":
        model_enum = ModelType.A14B
    elif model_str == "5B":
        model_enum = ModelType.FIVE_B
    else:
        model_enum = ModelType.A14B

    # ------------------------------------------------------------------
    # 9. Compute num_frames from duration + fps
    # ------------------------------------------------------------------
    fps = 16
    num_frames = int(duration_seconds * fps) + 1
    # Align to 4n+1 for Wan2.2 VAE
    if (num_frames - 1) % 4 != 0:
        num_frames = ((num_frames - 1) // 4 + 1) * 4 + 1

    # ------------------------------------------------------------------
    # 10. Handle upscale configuration (adjust gen dimensions)
    # ------------------------------------------------------------------
    postprocess = _get_postprocess_config(internal_config)
    enable_upscale = False
    upscale_model = ""
    upscale_resize = "1.5x"
    enable_interpolation = False
    interp_multiplier = 2
    interp_profile = "auto"
    enable_mmaudio = False
    mmaudio_prompt = ""
    mmaudio_negative_prompt = ""
    mmaudio_steps = 12
    mmaudio_cfg = 4.5

    gen_width, gen_height = width, height

    if isinstance(postprocess, dict):
        # Upscale
        upscale_config = postprocess.get("upscale", {})
        if upscale_config.get("enabled"):
            enable_upscale = True
            raw_model = upscale_config.get("model", "4x_foolhardy_Remacri")
            _upscale_model_map = {
                "4x-UltraSharp": "4x_NMKD-Siax_200k",
                "RealESRGAN_x4plus": "4x_NMKD-Siax_200k",
                "RealESRGAN_x2plus": "RealESRGAN_x2plus.pth",
                "realesrgan-x4plus": "4x_NMKD-Siax_200k",
            }
            upscale_model = _upscale_model_map.get(raw_model, raw_model)

            raw_resize = upscale_config.get("resize", 1.5)
            if isinstance(raw_resize, str):
                resize_factor = float(raw_resize.lower().rstrip("x"))
            else:
                resize_factor = float(raw_resize)
            # Snap to 0.5 steps
            resize_factor = round(resize_factor * 2) / 2
            if resize_factor < 1.0:
                resize_factor = 1.5

            upscale_resize = (
                f"{int(resize_factor)}x"
                if resize_factor == int(resize_factor)
                else f"{resize_factor}x"
            )

            gen_width = max(16, int(round(width / resize_factor / 16)) * 16)
            gen_height = max(16, int(round(height / resize_factor / 16)) * 16)

            # Ensure minimum generation size
            min_gen_dim = 320
            if gen_width < min_gen_dim or gen_height < min_gen_dim:
                max_factor_w = width / min_gen_dim
                max_factor_h = height / min_gen_dim
                resize_factor = min(resize_factor, max_factor_w, max_factor_h)
                resize_factor = max(1.0, round(resize_factor * 2) / 2)
                if resize_factor <= 1.0:
                    enable_upscale = False
                    gen_width, gen_height = width, height
                    logger.warning("[%s] Upscale disabled: resize clamped to 1.0x", workflow_id)
                else:
                    upscale_resize = (
                        f"{int(resize_factor)}x"
                        if resize_factor == int(resize_factor)
                        else f"{resize_factor}x"
                    )
                    gen_width = max(min_gen_dim, int(round(width / resize_factor / 16)) * 16)
                    gen_height = max(min_gen_dim, int(round(height / resize_factor / 16)) * 16)

            if enable_upscale:
                logger.info(
                    "[%s] Upscale: model=%s, resize=%s, gen=%dx%d -> target=%dx%d",
                    workflow_id, upscale_model, upscale_resize, gen_width, gen_height, width, height,
                )

        # Interpolation
        interp_config = postprocess.get("interpolation", {})
        if interp_config.get("enabled"):
            enable_interpolation = True
            interp_multiplier = interp_config.get("multiplier", 2)
            interp_profile = interp_config.get("profile", "auto")

        # MMAudio
        mmaudio_config = postprocess.get("mmaudio", {})
        if mmaudio_config.get("enabled"):
            enable_mmaudio = True
            mmaudio_prompt = mmaudio_config.get("prompt", "")
            mmaudio_negative_prompt = mmaudio_config.get("negative_prompt", "")
            mmaudio_steps = mmaudio_config.get("steps", 12)
            mmaudio_cfg = mmaudio_config.get("cfg", 4.5)

    # Use generation dimensions (may be reduced for upscale)
    final_width = gen_width if enable_upscale else width
    final_height = gen_height if enable_upscale else height

    # ------------------------------------------------------------------
    # 11. Build workflow via shared.workflow_builder
    # ------------------------------------------------------------------
    image_filename = None
    if first_frame_url:
        # The GPU worker will download this URL; we store it in task params
        image_filename = "__INPUT_IMAGE__"

    try:
        from shared.workflow_builder import build_workflow

        workflow = build_workflow(
            mode=gen_mode,
            model=model_enum,
            prompt=prompt,
            width=final_width,
            height=final_height,
            num_frames=num_frames,
            fps=fps,
            steps=video_steps,
            cfg=video_cfg,
            shift=video_shift,
            loras=lora_inputs if lora_inputs else None,
            scheduler=video_scheduler,
            image_filename=image_filename,
            noise_aug_strength=video_noise_aug,
            model_preset=video_model_preset,
            motion_amplitude=video_motion_amp,
            t5_preset=t5_preset,
        )
    except Exception as exc:
        logger.warning("WorkflowBuilder failed (%s), using minimal fallback", exc)
        workflow = {
            "_meta": {"version": "gateway_v1", "fallback": True},
            "prompt": prompt,
            "mode": gen_mode.value,
            "model": model_enum.value,
            "width": final_width,
            "height": final_height,
            "num_frames": num_frames,
            "fps": fps,
            "steps": video_steps,
            "cfg": video_cfg,
            "shift": video_shift,
        }

    # ------------------------------------------------------------------
    # 11b. Inject MMAudio nodes if enabled
    # ------------------------------------------------------------------
    if enable_mmaudio and not workflow.get("_meta", {}).get("fallback"):
        try:
            from shared.workflow_builder import _inject_mmaudio
            workflow = _inject_mmaudio(
                workflow,
                fps=fps,
                num_frames=num_frames,
                prompt=mmaudio_prompt,
                negative_prompt=mmaudio_negative_prompt,
                steps=mmaudio_steps,
                cfg=mmaudio_cfg,
            )
        except Exception as exc:
            logger.warning("MMAudio injection failed: %s", exc)

    # ------------------------------------------------------------------
    # 12. Build task params
    # ------------------------------------------------------------------
    task_params = {
        "prompt": prompt,
        "width": final_width,
        "height": final_height,
        "num_frames": num_frames,
        "fps": fps,
        "steps": video_steps,
        "cfg": video_cfg,
        "shift": video_shift,
        "scheduler": video_scheduler,
        "noise_aug_strength": video_noise_aug,
        "motion_amplitude": video_motion_amp,
        "model_preset": video_model_preset,
        "t5_preset": t5_preset,
        "clip_preset": clip_preset,
        "extract_last_frame": True,  # Always extract for potential continuation
    }

    # I2V-specific: include first frame URL for worker download
    if first_frame_url:
        task_params["first_frame_url"] = first_frame_url
        # Store as input_files for worker COS download
        task_params["input_files"] = [{
            "cos_url": first_frame_url,
            "placeholder": "__INPUT_IMAGE__",
        }]

    # LoRA info
    if lora_inputs:
        task_params["loras"] = [
            {
                "name": l.name,
                "strength": l.strength,
                "trigger_words": l.trigger_words,
                "trigger_prompt": l.trigger_prompt,
            }
            for l in lora_inputs
        ]

    # Upscale params
    if enable_upscale:
        task_params["enable_upscale"] = True
        task_params["upscale_model"] = upscale_model
        task_params["upscale_resize"] = upscale_resize

    # Interpolation params
    if enable_interpolation:
        task_params["enable_interpolation"] = True
        task_params["interpolation_multiplier"] = interp_multiplier
        task_params["interpolation_profile"] = interp_profile

    # MMAudio params
    if enable_mmaudio:
        task_params["enable_mmaudio"] = True
        task_params["mmaudio_prompt"] = mmaudio_prompt
        task_params["mmaudio_negative_prompt"] = mmaudio_negative_prompt
        task_params["mmaudio_steps"] = mmaudio_steps
        task_params["mmaudio_cfg"] = mmaudio_cfg

    # Continuation references
    if is_continuation and parent_workflow:
        parent_video = parent_workflow.get("final_video_url")
        if parent_video:
            task_params["parent_video_url"] = parent_video
        parent_chain = parent_workflow.get("chain_id")
        if parent_chain:
            task_params["parent_chain_id"] = parent_chain
        if origin_first_frame_url:
            task_params["initial_reference_url"] = origin_first_frame_url

    # ------------------------------------------------------------------
    # 13. Log generation parameters
    # ------------------------------------------------------------------
    logger.info("[VIDEO_PARAMS] %s - model=%s, resolution=%s -> %dx%d", workflow_id, video_model, video_resolution, final_width, final_height)
    logger.info("[VIDEO_PARAMS] %s - duration=%s -> %.1fs, frames=%d", workflow_id, video_duration, duration_seconds, num_frames)
    logger.info("[VIDEO_PARAMS] %s - steps=%d, cfg=%.1f, shift=%.1f, scheduler=%s", workflow_id, video_steps, video_cfg, video_shift, video_scheduler)
    logger.info("[VIDEO_PARAMS] %s - noise_aug=%.2f, motion_amp=%.2f", workflow_id, video_noise_aug, video_motion_amp)
    logger.info("[VIDEO_PARAMS] %s - mode=%s, loras=%s", workflow_id, gen_mode.value, [f"{l.name}:{l.strength}" for l in lora_inputs])
    logger.info("[VIDEO_PARAMS] %s - prompt=%s", workflow_id, prompt[:200])

    # ------------------------------------------------------------------
    # 14. Create chain via TaskGateway
    # ------------------------------------------------------------------
    chain_params = {
        "prompt": prompt,
        "model": model_enum.value,
        "width": final_width,
        "height": final_height,
        "duration": duration_seconds,
        "workflow_id": workflow_id,
    }
    chain_id = await gateway.create_chain(segment_count=1, params=chain_params)

    # Create task for the single segment
    task_id = await gateway.create_task(
        mode=gen_mode,
        model=model_enum,
        workflow=workflow,
        params=task_params,
        chain_id=chain_id,
    )

    # Store input_files on the task hash (for worker COS download)
    if first_frame_url:
        # Worker expects cos_key (relative path WITHOUT the cos_prefix).
        # COS URL: https://bucket.cos.region/cvid/frames/file.png
        # Worker calls download_file(subdir, filename) → make_key adds prefix "cvid/"
        # So cos_key should be "frames/file.png" (not "cvid/frames/file.png")
        cos_key = first_frame_url
        if "://" in cos_key:
            # Strip domain: https://bucket.cos.region.com/cvid/frames/file.png → cvid/frames/file.png
            cos_key = cos_key.split("/", 3)[-1] if cos_key.count("/") >= 3 else cos_key
        # Strip cos_prefix if present (e.g. "cvid/frames/file.png" → "frames/file.png")
        cos_prefix = getattr(config, 'cos_prefix', 'cvid')
        if cos_prefix and cos_key.startswith(cos_prefix + "/"):
            cos_key = cos_key[len(cos_prefix) + 1:]
        input_files = [{
            "cos_key": cos_key,
            "cos_url": first_frame_url,
            "placeholder": "__INPUT_IMAGE__",
            "original_filename": "first_frame.png",
        }]
        await redis.hset(task_key(task_id), mapping={
            "input_files": json.dumps(input_files),
        })

    # Set extract_last_frame at task hash top level (worker reads raw_data, not params)
    await redis.hset(task_key(task_id), "extract_last_frame", "1")

    # Update chain with task ID
    await redis.hset(chain_key(chain_id), mapping={
        "status": "running",
        "segment_task_ids": json.dumps([task_id]),
        "current_task_id": task_id,
    })

    logger.info("[%s] Chain %s created, task %s queued", workflow_id, chain_id, task_id)

    # Store actual generation dimensions in workflow hash for continuation inheritance
    await redis.hset(
        f"workflow:{workflow_id}",
        mapping={
            "actual_width": str(final_width),
            "actual_height": str(final_height),
            "chain_id": chain_id,
        },
    )

    # ------------------------------------------------------------------
    # 15. Poll for completion
    # ------------------------------------------------------------------
    final_video_url = None
    last_status = "unknown"
    deadline = time.time() + _MAX_POLL_SECONDS

    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        # Check task status directly (chain status is not auto-updated)
        task_data = await redis.hgetall(task_key(task_id))
        last_status = task_data.get("status", "unknown")

        if last_status == "completed":
            final_video_url = task_data.get("video_url")
            # Also update chain status
            await redis.hset(chain_key(chain_id), mapping={
                "status": "completed",
                "final_video_url": final_video_url or "",
            })
            logger.info("[%s] Task completed, video URL: %s", workflow_id, final_video_url)
            break
        elif last_status == "failed":
            error = task_data.get("error", "Unknown error")
            await redis.hset(chain_key(chain_id), mapping={"status": "failed", "error": error})
            logger.error("[%s] Task failed: %s", workflow_id, error)
            return VideoGenerationResult(
                chain_id=chain_id,
                video_url=None,
                loras_used=[
                    {"name": l.name, "strength": l.strength, "trigger_words": l.trigger_words, "trigger_prompt": l.trigger_prompt}
                    for l in lora_inputs
                ],
                prompt_used=prompt,
                width=final_width,
                height=final_height,
                error=f"Chain generation failed: {error}",
            )
        elif last_status == "partial":
            final_video_url = chain_data.get("final_video_url")
            error = chain_data.get("error", "")
            logger.warning("[%s] Chain partial: %s, video: %s", workflow_id, error, final_video_url)
            break

    if not final_video_url:
        error_msg = f"Chain {chain_id} polling timeout after {_MAX_POLL_SECONDS}s, last status: {last_status}"
        logger.error("[%s] %s", workflow_id, error_msg)
        return VideoGenerationResult(
            chain_id=chain_id,
            video_url=None,
            prompt_used=prompt,
            width=final_width,
            height=final_height,
            error=error_msg,
        )

    # Propagate last frame from chain to workflow for continuation support
    try:
        chain_data = await redis.hgetall(chain_key(chain_id))
        lossless_frame = chain_data.get("lossless_last_frame_url", "")
        last_frame = chain_data.get("last_frame_url", "")
        wf_update = {}
        if lossless_frame:
            wf_update["lossless_last_frame_url"] = lossless_frame
        if last_frame:
            wf_update["last_frame_url"] = last_frame
        if wf_update:
            await redis.hset(f"workflow:{workflow_id}", mapping=wf_update)
            logger.info("[%s] Propagated last frame to workflow: %s", workflow_id, lossless_frame or last_frame)
    except Exception as exc:
        logger.warning("[%s] Failed to propagate last frame: %s", workflow_id, exc)

    loras_info = [
        {"name": l.name, "strength": l.strength, "trigger_words": l.trigger_words, "trigger_prompt": l.trigger_prompt}
        for l in lora_inputs
    ]

    return VideoGenerationResult(
        chain_id=chain_id,
        video_url=final_video_url,
        loras_used=loras_info,
        prompt_used=prompt,
        width=final_width,
        height=final_height,
    )
