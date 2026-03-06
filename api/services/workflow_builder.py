import json
import copy
import random
import logging
import os
import yaml
from pathlib import Path
from typing import Optional
from api.config import WORKFLOWS_DIR, COMFYUI_PATH, LORAS_PATH
from api.models.enums import ModelType, GenerateMode
from api.models.schemas import LoraInput

logger = logging.getLogger(__name__)

LORAS_DIR = COMFYUI_PATH / "models" / "loras"


def _load_lora_name_map() -> dict[str, str]:
    """Load name -> file mapping from loras.yaml."""
    try:
        with open(LORAS_PATH) as f:
            data = yaml.safe_load(f)
        return {item["name"]: item["file"] for item in data.get("loras", []) if "name" in item and "file" in item}
    except Exception as e:
        logger.warning(f"Failed to load loras config: {e}")
        return {}


def _load_lora_keywords() -> dict[str, list[str]]:
    """Load name -> all keywords (trigger_words + example prompt tags) from loras.yaml."""
    try:
        with open(LORAS_PATH) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.warning(f"Failed to load loras config: {e}")
        return {}
    result = {}
    for item in data.get("loras", []):
        name = item.get("name")
        if not name:
            continue
        keywords = list(item.get("trigger_words", []))
        # Extract tag-style keywords from example prompts (short lines before first blank line)
        for ep in item.get("example_prompts", []):
            for line in ep.split("\n"):
                line = line.strip()
                if not line:
                    break  # stop at first blank line
                # Tag lines are short (< 30 chars) and contain no periods
                if len(line) < 30 and "." not in line and line not in keywords:
                    keywords.append(line)
        result[name] = keywords
    return result


_ANTONYM_GROUPS = [
    {"big", "huge", "large", "giant", "massive"},
    {"small", "tiny", "little", "petite", "mini"},
    {"tall", "long"},
    {"short"},
    {"fast", "rapid", "quick"},
    {"slow", "gentle", "soft"},
]


def _has_conflict(word: str, prompt_lower: str) -> bool:
    """Check if a trigger word semantically conflicts with the prompt."""
    word_tokens = set(word.lower().replace(",", " ").split())
    prompt_tokens = set(prompt_lower.replace(",", " ").split())
    for group in _ANTONYM_GROUPS:
        word_in_group = word_tokens & group
        if not word_in_group:
            continue
        # Find the opposing groups
        for other_group in _ANTONYM_GROUPS:
            if other_group is group:
                continue
            if prompt_tokens & other_group:
                return True
    return False


def _inject_trigger_words(prompt: str, loras: list[LoraInput]) -> str:
    """Collect trigger words and example tags from selected LoRAs and prepend to prompt."""
    kw_map = _load_lora_keywords()
    all_words = []
    prompt_lower = prompt.lower()
    for lora in loras:
        for word in kw_map.get(lora.name, []):
            # Skip extremely long text blocks
            if len(word) > 200:
                continue
            if word.lower() in prompt_lower or word in all_words:
                continue
            # Skip words that conflict with the prompt
            if _has_conflict(word, prompt_lower):
                logger.debug("Skipping conflicting trigger '%s' for prompt", word)
                continue
            all_words.append(word)
    if not all_words:
        return prompt
    return "\n".join(all_words) + "\n\n" + prompt


_lora_name_map: dict[str, str] = _load_lora_name_map()
DIFFUSION_DIR = COMFYUI_PATH / "models" / "diffusion_models"
TEXT_ENCODERS_DIR = COMFYUI_PATH / "models" / "text_encoders"

# T5 text encoder presets: name -> {file, quantization}
T5_PRESETS = {
    "default": {
        "file": "umt5-xxl-enc-fp8_e4m3fn.safetensors",
        "quantization": "disabled",  # auto-detected as fp8 from tensor dtype
    },
    "nsfw": {
        "file": "nsfw_wan_umt5-xxl_bf16.safetensors",
        "quantization": "fp8_e4m3fn",  # runtime quantization for VRAM efficiency
    },
}

# Model presets: name -> {high: filename, low: filename, quantization: str, recommended_params: dict}
MODEL_PRESETS = {
    "default": {
        "high": "Wan2_2-I2V-A14B-HIGH_bf16.safetensors",
        "low": "Wan2_2-I2V-A14B-LOW_bf16.safetensors",
        "quantization": "fp8_e4m3fn",
    },
    "nsfw_v2": {
        "high": "wan22EnhancedNSFWSVICamera_nsfwV2FP8H.safetensors",
        "low": "wan22EnhancedNSFWSVICamera_nsfwV2FP8L.safetensors",
        "quantization": "disabled",
        "recommended_params": {"steps": 4, "cfg": 1.0, "scheduler": "euler"},
    },
}


def get_t5_presets() -> list[dict]:
    """Return available T5 text encoder presets for the API."""
    result = []
    for name, info in T5_PRESETS.items():
        exists = (TEXT_ENCODERS_DIR / info["file"]).exists()
        result.append({"name": name, "file": info["file"], "quantization": info["quantization"], "available": exists})
    return result


def get_model_presets() -> list[dict]:
    """Return available model presets for the API."""
    result = []
    for name, info in MODEL_PRESETS.items():
        # Check if files exist
        h_exists = (DIFFUSION_DIR / info["high"]).exists()
        l_exists = (DIFFUSION_DIR / info["low"]).exists()
        entry = {
            "name": name,
            "high": info["high"],
            "low": info["low"],
            "quantization": info["quantization"],
            "available": h_exists and l_exists,
        }
        if "recommended_params" in info:
            entry["recommended_params"] = info["recommended_params"]
        result.append(entry)
    return result

WORKFLOW_MAP = {
    (GenerateMode.T2V, ModelType.A14B): "t2v_a14b.json",
    (GenerateMode.T2V, ModelType.FIVE_B): "t2v_5b.json",
    (GenerateMode.I2V, ModelType.A14B): "i2v_a14b.json",
    (GenerateMode.I2V, ModelType.FIVE_B): "i2v_5b.json",
}

_template_cache: dict[str, dict] = {}


def _load_template(name: str) -> dict:
    if name not in _template_cache:
        path = WORKFLOWS_DIR / name
        with open(path) as f:
            _template_cache[name] = json.load(f)
    return copy.deepcopy(_template_cache[name])


def _has_variant_tag(name: str, tag: str) -> bool:
    """Check if name contains a variant tag (HIGH/LOW) as a distinct segment, not as a substring of another word."""
    import re
    return bool(re.search(rf'(?:^|[\-_\s.])({tag})(?:[\-_\s.]|$)', name, re.IGNORECASE))


def _find_lora_file(base_name: str, variant: str) -> Optional[str]:
    """Find a LoRA file matching base_name and variant (high/low) in the loras directory."""
    if not variant:
        for f in LORAS_DIR.glob("*.safetensors"):
            if base_name in f.stem:
                return f.name
        return None
    # First try to find with variant tag (e.g. file-HIGH.safetensors)
    for f in LORAS_DIR.glob("*.safetensors"):
        fname = f.stem
        if base_name in fname and _has_variant_tag(fname, variant.upper()):
            return f.name
    # If no HIGH/LOW variant found, this might be a single-file LoRA — use it for both stages
    for f in LORAS_DIR.glob("*.safetensors"):
        if base_name in f.stem and not _has_variant_tag(f.stem, "HIGH") and not _has_variant_tag(f.stem, "LOW"):
            return f.name
    return None


def _inject_loras(workflow: dict, loras: list[LoraInput], model_node_ids: list[str]) -> dict:
    if not loras:
        return workflow
    # Find max numeric ID (handle both pure numbers and "prefix:number" format)
    max_id = 0
    for k in workflow.keys():
        # Extract numeric part from IDs like "1252:1299" or "917"
        if ':' in k:
            parts = k.split(':')
            for part in parts:
                if part.isdigit():
                    max_id = max(max_id, int(part))
        elif k.isdigit():
            max_id = max(max_id, int(k))

    for model_node_id in model_node_ids:
        # Determine if this is a HIGH or LOW model node
        model_name = workflow[model_node_id].get("inputs", {}).get("model", "")
        model_upper = model_name.upper()
        if "HIGH" in model_upper:
            variant = "high"
        elif "LOW" in model_upper:
            variant = "low"
        else:
            variant = None  # single-stage (5B), use any available

        prev_output = [model_node_id, 0]
        for i, lora in enumerate(loras):
            # Resolve config name to file base name
            base_name = _lora_name_map.get(lora.name, lora.name)
            # Resolve actual filename
            if variant:
                lora_file = _find_lora_file(base_name, variant)
            else:
                # For single-stage, try high first, then any match
                lora_file = _find_lora_file(base_name, "high")
                if not lora_file:
                    lora_file = _find_lora_file(base_name, "")
            if not lora_file:
                logger.warning(f"LoRA file not found for {lora.name} (resolved={base_name}) variant={variant}, skipping")
                continue

            new_id = str(max_id + 1)
            max_id += 1
            lora_node = {
                "class_type": "WanVideoLoraSelect",
                "inputs": {
                    "lora": lora_file,
                    "strength": lora.strength,
                    "low_mem_load": False,
                    "merge_loras": False,
                },
            }
            if prev_output[0] != model_node_id:
                lora_node["inputs"]["prev_lora"] = prev_output
            workflow[new_id] = lora_node
            prev_output = [new_id, 0]

        if prev_output[0] == model_node_id:
            continue  # no loras were added for this model

        # Insert WanVideoSetLoRAs node
        set_id = str(max_id + 1)
        max_id += 1
        workflow[set_id] = {
            "class_type": "WanVideoSetLoRAs",
            "inputs": {
                "model": [model_node_id, 0],
                "lora": prev_output,
            },
        }
        # Rewire: find nodes that consume this model and point them to set_id
        for nid, node in workflow.items():
            if nid == set_id:
                continue
            inputs = node.get("inputs", {})
            for key, val in inputs.items():
                if isinstance(val, list) and len(val) == 2 and val[0] == model_node_id and val[1] == 0:
                    if nid != set_id and key == "model":
                        inputs[key] = [set_id, 0]
    return workflow


def _bypass_color_match(workflow: dict):
    """Remove ColorMatch node and rewire consumers to read from its source directly."""
    cm_id = None
    for nid, node in workflow.items():
        if node.get("class_type") == "ColorMatch":
            cm_id = nid
            break
    if not cm_id:
        return
    decode_ref = workflow[cm_id]["inputs"].get("image_target")
    for nid, node in workflow.items():
        if nid == cm_id:
            continue
        inputs = node.get("inputs", {})
        for key, val in inputs.items():
            if isinstance(val, list) and len(val) == 2 and val[0] == cm_id:
                inputs[key] = decode_ref
    del workflow[cm_id]


UPSCALE_MODEL = "RealESRGAN_x2plus.pth"


def _inject_upscale(workflow: dict) -> dict:
    """Insert UpscaleModelLoader + ImageUpscaleWithModel between decode and VHS_VideoCombine."""
    # Find VHS_VideoCombine and its image source
    combine_id = None
    for nid, node in workflow.items():
        if node.get("class_type") == "VHS_VideoCombine":
            combine_id = nid
            break
    if not combine_id:
        logger.warning("No VHS_VideoCombine found, skipping upscale injection")
        return workflow

    images_input = workflow[combine_id]["inputs"].get("images")
    if not isinstance(images_input, list) or len(images_input) != 2:
        logger.warning("VHS_VideoCombine images input unexpected, skipping upscale")
        return workflow

    # Find max numeric ID (handle both pure numbers and "prefix:number" format)
    max_id = 0
    for k in workflow.keys():
        if ':' in k:
            # Extract all numeric parts from IDs like "1252:1299"
            for part in k.split(':'):
                if part.isdigit():
                    max_id = max(max_id, int(part))
        elif k.isdigit():
            max_id = max(max_id, int(k))

    # Add UpscaleModelLoader
    loader_id = str(max_id + 1)
    workflow[loader_id] = {
        "class_type": "UpscaleModelLoader",
        "inputs": {"model_name": UPSCALE_MODEL},
    }

    # Add ImageUpscaleWithModelBatched — processes video frames in sub-batches for VRAM efficiency
    upscale_id = str(max_id + 2)
    workflow[upscale_id] = {
        "class_type": "ImageUpscaleWithModelBatched",
        "inputs": {
            "upscale_model": [loader_id, 0],
            "images": images_input,  # was pointing to decode output
            "per_batch": 4,
        },
    }

    # Rewire VHS_VideoCombine to use upscaled output
    workflow[combine_id]["inputs"]["images"] = [upscale_id, 0]

    return workflow


def build_workflow(
    mode: GenerateMode,
    model: ModelType,
    prompt: str,
    negative_prompt: str = "",
    width: int = 848,
    height: int = 480,
    num_frames: int = 81,
    fps: int = 24,
    steps: int = 30,
    cfg: float = 6.0,
    shift: float = 5.0,
    seed: Optional[int] = None,
    loras: Optional[list[LoraInput]] = None,
    scheduler: str = "unipc",
    image_filename: Optional[str] = None,
    noise_aug_strength: float = 0.0,
    model_preset: str = "",
    motion_amplitude: float = 0.0,
    color_match: bool = True,
    color_match_method: str = "mkl",
    resize_mode: str = "crop_to_new",
    upscale: bool = False,
    t5_preset: str = "",
) -> dict:
    # Normalize loras: accept both LoraInput objects and dicts
    if loras:
        loras = [l if isinstance(l, LoraInput) else LoraInput(**l) for l in loras]
    # Always use standard workflow (enhanced workflow causes scene jumping issues)
    template_name = WORKFLOW_MAP[(mode, model)]
    workflow = _load_template(template_name)

    if seed is None:
        seed = random.randint(0, 1125899906842624)

    # Align num_frames to 4n+1 (required by Wan2.2 VAE)
    if (num_frames - 1) % 4 != 0:
        num_frames = ((num_frames - 1) // 4 + 1) * 4 + 1
        logger.info("Aligned num_frames to %d (must be 4n+1)", num_frames)

    # Align width/height to 16 (required by Wan2.2 VAE spatial downsample)
    if width % 16 != 0:
        width = (width // 16) * 16
        logger.info("Aligned width to %d (must be multiple of 16)", width)
    if height % 16 != 0:
        height = (height // 16) * 16
        logger.info("Aligned height to %d (must be multiple of 16)", height)

    # Apply model preset: override model filenames and quantization
    preset = MODEL_PRESETS.get(model_preset) if model_preset else None
    if preset:
        for node_id, node in workflow.items():
            if node.get("class_type") == "WanVideoModelLoader":
                inputs = node.get("inputs", {})
                cur_model = inputs.get("model", "").upper()
                if "HIGH" in cur_model:
                    inputs["model"] = preset["high"]
                elif "LOW" in cur_model:
                    inputs["model"] = preset["low"]
                inputs["quantization"] = preset["quantization"]
        # Apply recommended sampling params from preset
        rec = preset.get("recommended_params")
        if rec:
            steps = rec.get("steps", steps)
            cfg = rec.get("cfg", cfg)
            scheduler = rec.get("scheduler", scheduler)

    # Apply T5 text encoder preset
    t5_info = T5_PRESETS.get(t5_preset) if t5_preset else None
    if t5_info:
        for node_id, node in workflow.items():
            if node.get("class_type") == "LoadWanVideoT5TextEncoder":
                inputs = node.get("inputs", {})
                inputs["model_name"] = t5_info["file"]
                inputs["quantization"] = t5_info["quantization"]

    # Walk through nodes and set parameters
    final_prompt = _inject_trigger_words(prompt, loras) if loras else prompt
    for node_id, node in workflow.items():
        ct = node.get("class_type", "")
        inputs = node.get("inputs", {})

        if ct == "WanVideoTextEncode":
            inputs["positive_prompt"] = final_prompt
            inputs["negative_prompt"] = negative_prompt

        elif ct == "WanVideoSampler":
            inputs["steps"] = steps
            inputs["cfg"] = cfg
            inputs["shift"] = shift
            inputs["seed"] = seed
            inputs["scheduler"] = scheduler

        elif ct == "WanVideoImageToVideoEncode":
            inputs["width"] = width
            inputs["height"] = height
            inputs["num_frames"] = num_frames
            inputs["noise_aug_strength"] = noise_aug_strength
            if "augment_empty_frames" in inputs:
                inputs["augment_empty_frames"] = motion_amplitude

        elif ct == "ColorMatch":
            inputs["method"] = color_match_method

        elif ct == "WanVideoImageResizeToClosest":
            inputs["generation_width"] = width
            inputs["generation_height"] = height
            inputs["aspect_ratio_preservation"] = resize_mode

        elif ct == "VHS_VideoCombine":
            inputs["frame_rate"] = fps

        # Set uploaded image for I2V
        elif ct == "LoadImage" and image_filename:
            inputs["image"] = image_filename

    # Bypass ColorMatch if disabled
    if not color_match:
        _bypass_color_match(workflow)

    # Dynamic step split for two-stage A14B (instead of hardcoded 15)
    if model == ModelType.A14B:
        split_step = steps // 2
        for node_id, node in workflow.items():
            if node.get("class_type") == "WanVideoSampler":
                inp = node.get("inputs", {})
                if inp.get("start_step", 0) == 0 and inp.get("end_step", -1) > 0:
                    inp["end_step"] = split_step
                elif inp.get("end_step", 0) == -1 and inp.get("start_step", 0) > 0:
                    inp["start_step"] = split_step

    # Inject LoRAs
    if loras:
        model_node_ids = [
            nid for nid, n in workflow.items()
            if n.get("class_type") == "WanVideoModelLoader"
        ]
        workflow = _inject_loras(workflow, loras, model_node_ids)

    # Inject upscale nodes if enabled
    if upscale:
        workflow = _inject_upscale(workflow)

    return workflow


# ── Story mode (PainterI2V / PainterLongVideo) ──────────────────────

# UNETLoader model presets (same files, different loader class)
STORY_MODEL_PRESETS = {
    "nsfw_v2": {
        "high": "wan22EnhancedNSFWSVICamera_nsfwV2FP8H.safetensors",
        "low": "wan22EnhancedNSFWSVICamera_nsfwV2FP8L.safetensors",
    },
    "default": {
        "high": "Wan2_2-I2V-A14B-HIGH_bf16.safetensors",
        "low": "Wan2_2-I2V-A14B-LOW_bf16.safetensors",
    },
}

# CLIPLoader presets for story mode
STORY_CLIP_PRESETS = {
    "nsfw": "nsfw_wan_umt5-xxl_fp8_scaled.safetensors",
    "default": "umt5-xxl-enc-bf16.safetensors",
}


def get_story_model_presets() -> list[dict]:
    """Return available story model presets for the API."""
    result = []
    for name, info in STORY_MODEL_PRESETS.items():
        h_exists = (DIFFUSION_DIR / info["high"]).exists()
        l_exists = (DIFFUSION_DIR / info["low"]).exists()
        result.append({"name": name, "high": info["high"], "low": info["low"], "available": h_exists and l_exists})
    return result


def get_story_clip_presets() -> list[dict]:
    """Return available CLIP presets for story mode."""
    result = []
    for name, filename in STORY_CLIP_PRESETS.items():
        exists = (TEXT_ENCODERS_DIR / filename).exists()
        result.append({"name": name, "file": filename, "available": exists})
    return result


def _inject_story_loras(workflow: dict, loras: list[LoraInput]) -> dict:
    """Inject Power Lora Loader (rgthree) nodes between UNETLoader and WanMoeKSamplerAdvanced.

    For each UNETLoader (HIGH/LOW), chain LoRA loaders and rewire
    the sampler's model_high_noise / model_low_noise to the last loader.
    """
    if not loras:
        return workflow

    # Find UNETLoader nodes and determine HIGH/LOW
    unet_nodes = {}  # "high" or "low" -> node_id
    for nid, node in workflow.items():
        if node.get("class_type") == "UNETLoader":
            title = node.get("_meta", {}).get("title", "").upper()
            unet_name = node.get("inputs", {}).get("unet_name", "").upper()
            if "HIGH" in title or "HIGH" in unet_name:
                unet_nodes["high"] = nid
            elif "LOW" in title or "LOW" in unet_name:
                unet_nodes["low"] = nid

    if not unet_nodes:
        logger.warning("No UNETLoader nodes found for story LoRA injection")
        return workflow

    # Find max numeric ID (handle both pure numbers and "prefix:number" format)
    max_id = 0
    for k in workflow.keys():
        if ':' in k:
            # Extract all numeric parts from IDs like "1252:1299"
            for part in k.split(':'):
                if part.isdigit():
                    max_id = max(max_id, int(part))
        elif k.isdigit():
            max_id = max(max_id, int(k))

    for variant, unet_nid in unet_nodes.items():
        # Build a chain of Power Lora Loader nodes
        prev_output = [unet_nid, 0]
        last_lora_id = None

        for lora in loras:
            base_name = _lora_name_map.get(lora.name, lora.name)
            lora_file = _find_lora_file(base_name, variant)
            if not lora_file:
                # Try single-file LoRA (no HIGH/LOW variant)
                lora_file = _find_lora_file(base_name, "")
            if not lora_file:
                logger.warning("Story LoRA file not found for %s variant=%s, skipping", lora.name, variant)
                continue

            max_id += 1
            new_id = str(max_id)
            workflow[new_id] = {
                "class_type": "Power Lora Loader (rgthree)",
                "inputs": {
                    "PowerLoraLoaderHeaderWidget": {"type": "PowerLoraLoaderHeaderWidget"},
                    "➕ Add Lora": "",
                    "lora_1": {
                        "on": True,
                        "lora": lora_file,
                        "strength": lora.strength,
                        "strengthTwo": lora.strength,
                    },
                    "model": prev_output,
                },
                "_meta": {
                    "title": f"LoRA {variant.upper()}"
                },
            }
            prev_output = [new_id, 0]
            last_lora_id = new_id

        if last_lora_id is None:
            continue  # no loras added for this variant

        # Rewire: find WanMoeKSamplerAdvanced and update model_high_noise / model_low_noise
        target_key = "model_high_noise" if variant == "high" else "model_low_noise"
        for nid, node in workflow.items():
            if node.get("class_type") == "WanMoeKSamplerAdvanced":
                inputs = node.get("inputs", {})
                old_ref = inputs.get(target_key)
                if isinstance(old_ref, list) and old_ref[0] == unet_nid:
                    inputs[target_key] = [last_lora_id, 0]

    return workflow


def build_story_workflow(
    is_first_segment: bool,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    num_frames: int,
    seed: Optional[int],
    shift: float,
    cfg: float,
    steps: int = 20,
    motion_amplitude: float = 1.15,
    motion_frames: int = 5,
    boundary: float = 0.9,
    image_filename: str = "",
    initial_ref_filename: str = "",
    video_filename: str = "",
    model_preset: str = "nsfw_v2",
    clip_preset: str = "nsfw",
    fps: int = 16,
    upscale: bool = False,
    loras: Optional[list[LoraInput]] = None,
) -> dict:
    """Build a Story workflow using PainterI2V (first segment) or PainterLongVideo (continuation).

    Args:
        is_first_segment: True for first segment (PainterI2V), False for continuation (PainterLongVideo).
        image_filename: For seg1, the start image. For seg2+, the previous segment's last frame.
        initial_ref_filename: For seg2+, the original first-frame image (identity anchor).
    """
    if is_first_segment:
        template_name = "i2v_a14b_story_first.json"
    else:
        template_name = "i2v_a14b_story_continue.json"

    workflow = _load_template(template_name)

    if seed is None:
        seed = random.randint(0, 1125899906842624)

    # Align num_frames to 4n+1
    if (num_frames - 1) % 4 != 0:
        num_frames = ((num_frames - 1) // 4 + 1) * 4 + 1
        logger.info("Story: aligned num_frames to %d", num_frames)

    # Align width/height to 16
    if width % 16 != 0:
        width = (width // 16) * 16
    if height % 16 != 0:
        height = (height // 16) * 16

    # Resolve model preset
    model_info = STORY_MODEL_PRESETS.get(model_preset, STORY_MODEL_PRESETS["nsfw_v2"])
    clip_file = STORY_CLIP_PRESETS.get(clip_preset, STORY_CLIP_PRESETS["nsfw"])

    # Inject trigger words from LoRAs into prompt
    if loras:
        normalized_for_tw = [l if isinstance(l, LoraInput) else LoraInput(**l) for l in loras]
        final_prompt = _inject_trigger_words(prompt, normalized_for_tw)
    else:
        final_prompt = prompt

    for node_id, node in workflow.items():
        ct = node.get("class_type", "")
        inputs = node.get("inputs", {})

        # UNETLoader — set model filenames
        if ct == "UNETLoader":
            cur = inputs.get("unet_name", "").upper()
            if "HIGH" in cur or "HIGH" in node.get("_meta", {}).get("title", "").upper():
                inputs["unet_name"] = model_info["high"]
            elif "LOW" in cur or "LOW" in node.get("_meta", {}).get("title", "").upper():
                inputs["unet_name"] = model_info["low"]

        # CLIPLoader — set clip model
        elif ct == "CLIPLoader":
            inputs["clip_name"] = clip_file

        # CLIPTextEncode — set prompts
        elif ct == "CLIPTextEncode":
            title = node.get("_meta", {}).get("title", "").lower()
            if "negative" in title:
                inputs["text"] = negative_prompt
            elif "positive" in title:
                inputs["text"] = final_prompt

        # PainterI2V — first segment
        elif ct == "PainterI2V":
            inputs["width"] = width
            inputs["height"] = height
            if num_frames > 0:
                inputs["length"] = num_frames
            inputs["motion_amplitude"] = motion_amplitude
            inputs["clip_vision_output"] = ["cv_encode", 0]

        # PainterLongVideo — continuation segment
        elif ct == "PainterLongVideo":
            inputs["width"] = width
            inputs["height"] = height
            if num_frames > 0:
                inputs["length"] = num_frames
            inputs["motion_frames"] = motion_frames
            inputs["motion_amplitude"] = motion_amplitude
            inputs["clip_vision_output"] = ["cv_encode", 0]

        # WanMoeKSamplerAdvanced — sampling params
        elif ct == "WanMoeKSamplerAdvanced":
            inputs["steps"] = steps
            inputs["boundary"] = boundary
            inputs["cfg_high_noise"] = cfg
            inputs["cfg_low_noise"] = cfg
            inputs["sigma_shift"] = shift

        # Seed
        elif ct == "Seed (rgthree)":
            inputs["seed"] = seed

        # VHS_VideoCombine — frame rate
        elif ct == "VHS_VideoCombine":
            inputs["frame_rate"] = fps

        # LoadImage — set image filenames
        elif ct == "LoadImage":
            title = node.get("_meta", {}).get("title", "").lower()
            if is_first_segment:
                # First segment: only one LoadImage for start_image
                inputs["image"] = image_filename
            else:
                # Continuation: two LoadImages
                if "previous" in title or "frame" in title:
                    if video_filename:
                        pass  # Will be replaced with VHS_LoadVideo below
                    else:
                        inputs["image"] = image_filename
                elif "initial" in title or "reference" in title:
                    inputs["image"] = initial_ref_filename
                else:
                    # Fallback: node "10" is previous, node "11" is reference
                    if node_id == "10":
                        if video_filename:
                            pass  # Will be replaced with VHS_LoadVideo below
                        else:
                            inputs["image"] = image_filename
                    elif node_id == "11":
                        inputs["image"] = initial_ref_filename

    # For continuation with video: replace LoadImage+ImageScaleBy with VHS_LoadVideo
    if not is_first_segment and video_filename:
        # Remove old LoadImage node "10" and ImageScaleBy node "55"
        workflow.pop("10", None)
        workflow.pop("55", None)

        # Add VHS_LoadVideo node to load previous video (pre-trimmed to last N frames)
        workflow["10"] = {
            "class_type": "VHS_LoadVideo",
            "inputs": {
                "video": video_filename,
                "force_rate": 0,
                "custom_width": 0,
                "custom_height": 0,
                "frame_load_cap": 0,
                "skip_first_frames": 0,
                "select_every_nth": 1,
            },
            "_meta": {"title": "Load Previous Video"},
        }

        # Rewire PainterLongVideo.previous_video to point directly to VHS_LoadVideo
        for nid, node in workflow.items():
            if node.get("class_type") == "PainterLongVideo":
                node["inputs"]["previous_video"] = ["10", 0]

    # CLIP Vision: load model + encode reference image for character consistency
    # Find the LoadImage node for reference image
    ref_image_node = "11" if not is_first_segment else None
    # For first segment, find any LoadImage node
    if is_first_segment:
        for nid, node in workflow.items():
            if node.get("class_type") == "LoadImage":
                ref_image_node = nid
                break
    if ref_image_node:
        workflow["cv_loader"] = {
            "class_type": "CLIPVisionLoader",
            "inputs": {"clip_name": "clip_vision_h.safetensors"},
            "_meta": {"title": "Load CLIP Vision"},
        }
        workflow["cv_encode"] = {
            "class_type": "CLIPVisionEncode",
            "inputs": {
                "clip_vision": ["cv_loader", 0],
                "image": [ref_image_node, 0],
                "crop": "center",
            },
            "_meta": {"title": "CLIP Vision Encode"},
        }

    # Inject LoRAs via Power Lora Loader (rgthree)
    if loras:
        normalized = [l if isinstance(l, LoraInput) else LoraInput(**l) for l in loras]
        workflow = _inject_story_loras(workflow, normalized)

    # Inject upscale if enabled
    if upscale:
        workflow = _inject_upscale(workflow)

    return workflow


def _inject_story_postproc(workflow: dict, seg: dict) -> dict:
    """Inject post-processing nodes (TRT upscale, RIFE, MMAudio) into a single-segment story workflow.

    Finds the VHS_VideoCombine node and inserts post-processing between its image source and it.
    """
    # Find VHS_VideoCombine (final output)
    combine_id = None
    for nid, node in workflow.items():
        if node.get("class_type") == "VHS_VideoCombine" and node.get("inputs", {}).get("save_output", True):
            combine_id = nid
            break
    if not combine_id:
        for nid, node in workflow.items():
            if node.get("class_type") == "VHS_VideoCombine":
                combine_id = nid
                break
    if not combine_id:
        logger.warning("No VHS_VideoCombine found, skipping post-processing injection")
        return workflow

    current_image_ref = workflow[combine_id]["inputs"].get("images")
    if not isinstance(current_image_ref, list):
        logger.warning("VHS_VideoCombine images input unexpected, skipping post-processing")
        return workflow

    fps = seg.get("fps", 16)
    output_fps = fps

    # VRAMCleanup before post-processing: offload models to free VRAM for TRT engines
    if seg.get("enable_upscale") or seg.get("enable_interpolation"):
        workflow["pp_vram_cleanup"] = {
            "class_type": "VRAMCleanup",
            "inputs": {
                "offload_model": True,
                "offload_cache": True,
                "anything": current_image_ref,
            },
            "_meta": {"title": "VRAM Cleanup (pre-postproc)"},
        }
        current_image_ref = ["pp_vram_cleanup", 0]

    # TRT Upscale
    if seg.get("enable_upscale"):
        workflow["pp_upscale_loader"] = {
            "class_type": "LoadUpscalerTensorrtModel",
            "inputs": {
                "model": seg.get("upscale_model", "4x-UltraSharp"),
                "precision": "fp16",
            },
            "_meta": {"title": "Load Upscaler TRT"},
        }
        workflow["pp_upscale"] = {
            "class_type": "UpscalerTensorrt",
            "inputs": {
                "images": current_image_ref,
                "upscaler_trt_model": ["pp_upscale_loader", 0],
                "resize_to": seg.get("upscale_resize", "2x"),
                "resize_width": 1024,
                "resize_height": 1024,
            },
            "_meta": {"title": "Upscaler TensorRT"},
        }
        current_image_ref = ["pp_upscale", 0]

    # RIFE Frame Interpolation
    multiplier = seg.get("interpolation_multiplier", 2)
    if seg.get("enable_interpolation") and multiplier > 1:
        output_fps = fps * multiplier
        rife_profile = "large" if seg.get("enable_upscale") else seg.get("interpolation_profile", "small")
        workflow["pp_rife_loader"] = {
            "class_type": "AutoLoadRifeTensorrtModel",
            "inputs": {
                "model": "rife49_ensemble_True_scale_1_sim",
                "precision": "fp16",
                "resolution_profile": rife_profile,
            },
            "_meta": {"title": "Load RIFE TRT"},
        }
        workflow["pp_rife"] = {
            "class_type": "AutoRifeTensorrt",
            "inputs": {
                "frames": current_image_ref,
                "rife_trt_model": ["pp_rife_loader", 0],
                "clear_cache_after_n_frames": 100,
                "multiplier": multiplier,
                "keep_model_loaded": False,
            },
            "_meta": {"title": f"RIFE {output_fps}FPS"},
        }
        current_image_ref = ["pp_rife", 0]

    # MMAudio
    audio_ref = None
    if seg.get("enable_mmaudio"):
        workflow["pp_mma_model"] = {
            "class_type": "MMAudioModelLoader",
            "inputs": {
                "mmaudio_model": "mmaudio_large_44k_nsfw_gold_8.5k_final_fp16.safetensors",
                "base_precision": "fp16",
            },
            "_meta": {"title": "MMAudio Model"},
        }
        workflow["pp_mma_features"] = {
            "class_type": "MMAudioFeatureUtilsLoader",
            "inputs": {
                "vae_model": "mmaudio_vae_44k_fp16.safetensors",
                "synchformer_model": "mmaudio_synchformer_fp16.safetensors",
                "clip_model": "apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors",
                "mode": "44k",
                "precision": "fp16",
            },
            "_meta": {"title": "MMAudio Features"},
        }
        num_frames = seg.get("num_frames", 81)
        audio_duration = num_frames / fps
        workflow["pp_mma_sampler"] = {
            "class_type": "MMAudioSampler",
            "inputs": {
                "mmaudio_model": ["pp_mma_model", 0],
                "feature_utils": ["pp_mma_features", 0],
                "duration": audio_duration,
                "steps": seg.get("mmaudio_steps", 25),
                "cfg": seg.get("mmaudio_cfg", 4.5),
                "seed": random.randint(0, 1125899906842624),
                "prompt": seg.get("mmaudio_prompt", ""),
                "negative_prompt": seg.get("mmaudio_negative_prompt", ""),
                "mask_away_clip": False,
                "force_offload": True,
                "images": current_image_ref,
                "source_fps": float(fps),
            },
            "_meta": {"title": "MMAudio Sampler"},
        }
        audio_ref = ["pp_mma_sampler", 0]

    # Rewire VHS_VideoCombine
    workflow[combine_id]["inputs"]["images"] = current_image_ref
    workflow[combine_id]["inputs"]["frame_rate"] = output_fps
    if audio_ref:
        workflow[combine_id]["inputs"]["audio"] = audio_ref
        workflow[combine_id]["inputs"]["trim_to_audio"] = True

    return workflow


def build_merged_story_workflow(
    segments: list[dict],
    width: int,
    height: int,
    shift: float,
    cfg: float,
    steps: int = 5,
    motion_amplitude: float = 1.15,
    motion_frames: int = 5,
    boundary: float = 0.9,
    image_filename: str = "",
    model_preset: str = "nsfw_v2",
    clip_preset: str = "nsfw",
    fps: int = 16,
    upscale: bool = False,
    loras: Optional[list[LoraInput]] = None,
    match_image_ratio: bool = False,
    enable_upscale: bool = False,
    upscale_model: str = "4x-UltraSharp",
    upscale_resize: str = "2x",
    enable_interpolation: bool = False,
    interpolation_multiplier: int = 2,
    interpolation_profile: str = "small",
    enable_mmaudio: bool = False,
    mmaudio_prompt: str = "",
    mmaudio_negative_prompt: str = "",
    mmaudio_steps: int = 25,
    mmaudio_cfg: float = 4.5,
) -> dict:
    """Build a single merged ComfyUI workflow containing N story segments.

    Fully aligned with original WAN2.2-I2V-AutoPromptStory.json (82 nodes).
    """
    if loras:
        loras = [l if isinstance(l, LoraInput) else LoraInput(**l) for l in loras]

    # Align width/height to 16
    if width % 16 != 0:
        width = (width // 16) * 16
    if height % 16 != 0:
        height = (height // 16) * 16

    # Resolve presets
    model_info = STORY_MODEL_PRESETS.get(model_preset, STORY_MODEL_PRESETS["nsfw_v2"])
    clip_file = STORY_CLIP_PRESETS.get(clip_preset, STORY_CLIP_PRESETS["nsfw"])

    workflow: dict = {}

    # ═══════════════════════════════════════════════════════════════════════════
    # GLOBAL SHARED NODES (IDs 1-20)
    # ═══════════════════════════════════════════════════════════════════════════

    # Node 1: UNETLoader HIGH
    workflow["917"] = {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": model_info["high"],
            "weight_dtype": "default",
        },
        "_meta": {"title": "Load Diffusion Model HIGH"},
    }

    # Node 2: UNETLoader LOW
    workflow["918"] = {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": model_info["low"],
            "weight_dtype": "default",
        },
        "_meta": {"title": "Load Diffusion Model LOW"},
    }

    # Node 3: VAELoader
    workflow["916"] = {
        "class_type": "VAELoader",
        "inputs": {
            "vae_name": "wan_2.1_vae.safetensors",
        },
        "_meta": {"title": "加载VAE"},
    }

    # Node 4: CLIPLoader
    workflow["1521"] = {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": clip_file,
            "type": "wan",
            "device": "default",
        },
        "_meta": {"title": "加载CLIP"},
    }

    # Node 5: PathchSageAttentionKJ HIGH
    workflow["1252:1278"] = {
        "class_type": "PathchSageAttentionKJ",
        "inputs": {
            "sage_attention": "auto",
            "allow_compile": False,
            "model": ["917", 0],
        },
        "_meta": {"title": "Patch Sage Attention KJ"},
    }

    # Node 6: PathchSageAttentionKJ LOW
    workflow["1252:1281"] = {
        "class_type": "PathchSageAttentionKJ",
        "inputs": {
            "sage_attention": "auto",
            "allow_compile": False,
            "model": ["918", 0],
        },
        "_meta": {"title": "Patch Sage Attention KJ"},
    }

    # Node 7: ModelPatchTorchSettings HIGH
    workflow["1252:1279"] = {
        "class_type": "ModelPatchTorchSettings",
        "inputs": {
            "enable_fp16_accumulation": True,
            "model": ["1252:1278", 0],
        },
        "_meta": {"title": "Model Patch Torch Settings"},
    }

    # Node 8: ModelPatchTorchSettings LOW
    workflow["1252:1280"] = {
        "class_type": "ModelPatchTorchSettings",
        "inputs": {
            "enable_fp16_accumulation": True,
            "model": ["1252:1281", 0],
        },
        "_meta": {"title": "Model Patch Torch Settings"},
    }

    # Node 9: LoadImage (seg0 only)
    workflow["97"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_filename},
        "_meta": {"title": "加载图像"},
    }

    # CLIP Vision: load model + encode reference image for character consistency
    workflow["cv_loader"] = {
        "class_type": "CLIPVisionLoader",
        "inputs": {"clip_name": "clip_vision_h.safetensors"},
        "_meta": {"title": "Load CLIP Vision"},
    }
    workflow["cv_encode"] = {
        "class_type": "CLIPVisionEncode",
        "inputs": {
            "clip_vision": ["cv_loader", 0],
            "image": ["97", 0],
            "crop": "center",
        },
        "_meta": {"title": "CLIP Vision Encode"},
    }

    # Node 10: mxSlider (Length)
    workflow["1282"] = {
        "class_type": "mxSlider",
        "inputs": {
            "Xi": segments[0].get("num_frames", 81),
            "Xf": segments[0].get("num_frames", 81),
            "isfloatX": 0,
        },
        "_meta": {"title": "Lenght"},
    }

    # Node 11: mxSlider (Steps)
    workflow["1283"] = {
        "class_type": "mxSlider",
        "inputs": {
            "Xi": steps,
            "Xf": steps,
            "isfloatX": 0,
        },
        "_meta": {"title": "Steps"},
    }

    # Node 12: FloatConstant (motion amplitude)
    workflow["604"] = {
        "class_type": "FloatConstant",
        "inputs": {"value": motion_amplitude},
        "_meta": {"title": "motion amplitude"},
    }

    # Node 13: INTConstant (motion_frames)
    workflow["605"] = {
        "class_type": "INTConstant",
        "inputs": {"value": motion_frames},
        "_meta": {"title": "motion_frames"},
    }

    # Node 14: PrimitiveFloat (Sigma Shift)
    workflow["1551"] = {
        "class_type": "PrimitiveFloat",
        "inputs": {"value": shift},
        "_meta": {"title": "Sigma Shift"},
    }

    # Node 15: SamplerSelector
    workflow["1480"] = {
        "class_type": "SamplerSelector",
        "inputs": {"sampler_name": "euler"},
        "_meta": {"title": "Sampler Selector"},
    }

    # Node 16: SchedulerSelector
    workflow["1481"] = {
        "class_type": "SchedulerSelector",
        "inputs": {"scheduler": "simple"},
        "_meta": {"title": "Scheduler Selector"},
    }

    # Width/height: either from FindPerfectResolution or direct values
    if match_image_ratio:
        workflow["1445"] = {
            "class_type": "FindPerfectResolution",
            "inputs": {
                "desired_width": width,
                "desired_height": height,
                "divisible_by": 16,
                "upscale": False,
                "upscale_method": "lanczos",
                "small_image_mode": "none",
                "pad_color": "#000000",
                "image": ["97", 0],
            },
            "_meta": {"title": "Find Perfect Resolution"},
        }
        painter_width = ["1445", 0]
        painter_height = ["1445", 1]
    else:
        painter_width = width
        painter_height = height

    # ═══════════════════════════════════════════════════════════════════════════
    # PER-SEGMENT NODES (dynamic: supports N segments)
    # ═══════════════════════════════════════════════════════════════════════════

    # Generate unique node IDs per segment using "s{idx}_" prefix
    seg_node_ids = []  # list of dicts with node IDs per segment

    for seg_idx, seg in enumerate(segments):
        p = f"s{seg_idx}_"  # unique prefix per segment
        ids = {
            "lora_high": f"{p}lora_h",
            "lora_low": f"{p}lora_l",
            "clip_pos": f"{p}clip_pos",
            "clip_neg": f"{p}clip_neg",
            "seed": f"{p}seed",
            "pre_vram": f"{p}pre_vram",
            "painter": f"{p}painter",
            "sampler": f"{p}sampler",
            "vram": f"{p}vram",
            "vae_decode": f"{p}vae_dec",
            "batch": f"{p}batch",
        }
        seg_node_ids.append(ids)

        prompt = seg.get("prompt", "")
        negative_prompt = seg.get("negative_prompt", "")
        num_frames = seg.get("num_frames", 81)
        seed = seg.get("seed")
        if seed is None:
            seed = random.randint(0, 1125899906842624)

        # Align num_frames to 4n+1
        if (num_frames - 1) % 4 != 0:
            num_frames = ((num_frames - 1) // 4 + 1) * 4 + 1

        # Inject trigger words from LoRAs
        if loras:
            final_prompt = _inject_trigger_words(prompt, loras)
        else:
            final_prompt = prompt

        # Power Lora Loader (HIGH and LOW) — connect to ModelPatchTorchSettings
        workflow[ids["lora_high"]] = {
            "class_type": "Power Lora Loader (rgthree)",
            "inputs": {
                "PowerLoraLoaderHeaderWidget": {"type": "PowerLoraLoaderHeaderWidget"},
                "➕ Add Lora": "",
                "model": ["1252:1279", 0],
            },
            "_meta": {"title": f"{seg_idx + 1}LORA HIGH"},
        }
        workflow[ids["lora_low"]] = {
            "class_type": "Power Lora Loader (rgthree)",
            "inputs": {
                "PowerLoraLoaderHeaderWidget": {"type": "PowerLoraLoaderHeaderWidget"},
                "➕ Add Lora": "",
                "model": ["1252:1280", 0],
            },
            "_meta": {"title": f"{seg_idx + 1}LORA LOW"},
        }

        # CLIPTextEncode (Positive / Negative)
        workflow[ids["clip_pos"]] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": final_prompt, "clip": ["1521", 0]},
            "_meta": {"title": "Positive encode"},
        }
        workflow[ids["clip_neg"]] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt, "clip": ["1521", 0]},
            "_meta": {"title": "CLIP Text Encode (Negative Prompt)"},
        }

        # Seed
        workflow[ids["seed"]] = {
            "class_type": "Seed (rgthree)",
            "inputs": {"seed": seed},
            "_meta": {"title": f"{seg_idx + 1}-Seed"},
        }

        # Pre-Painter VRAMCleanup
        workflow[ids["pre_vram"]] = {
            "class_type": "VRAMCleanup",
            "inputs": {
                "offload_model": True,
                "offload_cache": True,
                "anything": [ids["clip_neg"], 0],
            },
            "_meta": {"title": f"Pre-Painter VRAM Cleanup seg{seg_idx}"},
        }

        # PainterI2V (seg0) or PainterLongVideo (seg1+)
        if seg_idx == 0:
            workflow[ids["painter"]] = {
                "class_type": "PainterI2V",
                "inputs": {
                    "width": painter_width,
                    "height": painter_height,
                    "length": ["1282", 0],
                    "batch_size": 1,
                    "motion_amplitude": ["604", 0],
                    "positive": [ids["clip_pos"], 0],
                    "negative": [ids["pre_vram"], 0],
                    "vae": ["916", 0],
                    "start_image": ["97", 0],
                    "clip_vision_output": ["cv_encode", 0],
                },
                "_meta": {"title": "PainterI2V"},
            }
        else:
            prev_ids = seg_node_ids[seg_idx - 1]
            workflow[ids["painter"]] = {
                "class_type": "PainterLongVideo",
                "inputs": {
                    "width": painter_width,
                    "height": painter_height,
                    "length": ["1282", 0],
                    "batch_size": 1,
                    "motion_frames": ["605", 0],
                    "motion_amplitude": ["604", 0],
                    "positive": [ids["clip_pos"], 0],
                    "negative": [ids["pre_vram"], 0],
                    "vae": ["916", 0],
                    "previous_video": [prev_ids["vae_decode"], 0],
                    "initial_reference_image": ["97", 0],
                    "clip_vision_output": ["cv_encode", 0],
                },
                "_meta": {"title": f"{seg_idx + 1}-PainterLongVideo"},
            }

        # WanMoeKSamplerAdvanced
        workflow[ids["sampler"]] = {
            "class_type": "WanMoeKSamplerAdvanced",
            "inputs": {
                "boundary": boundary,
                "add_noise": "enable",
                "noise_seed": [ids["seed"], 0],
                "steps": ["1283", 0],
                "cfg_high_noise": cfg,
                "cfg_low_noise": cfg,
                "sampler_name": ["1480", 0],
                "scheduler": ["1481", 0],
                "sigma_shift": ["1551", 0],
                "start_at_step": 0,
                "end_at_step": 10000,
                "return_with_leftover_noise": "disable",
                "model_high_noise": [ids["lora_high"], 0],
                "model_low_noise": [ids["lora_low"], 0],
                "positive": [ids["painter"], 0],
                "negative": [ids["painter"], 1],
                "latent_image": [ids["painter"], 2],
            },
            "_meta": {"title": f"Wan MoE KSampler seg{seg_idx}"},
        }

        # VRAMCleanup (post-sampler)
        workflow[ids["vram"]] = {
            "class_type": "VRAMCleanup",
            "inputs": {
                "offload_model": True,
                "offload_cache": True,
                "anything": [ids["sampler"], 0],
            },
            "_meta": {"title": f"VRAM-Cleanup seg{seg_idx}"},
        }

        # VAEDecode
        workflow[ids["vae_decode"]] = {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": [ids["vram"], 0],
                "vae": ["916", 0],
            },
            "_meta": {"title": f"VAE解码 seg{seg_idx}"},
        }

        # ImageBatchMulti (merge with previous segments)
        if seg_idx > 0:
            prev_ids = seg_node_ids[seg_idx - 1]
            # seg1 merges with seg0's vae_decode; seg2+ merges with prev batch
            prev_image_ref = prev_ids["vae_decode"] if seg_idx == 1 else prev_ids["batch"]
            workflow[ids["batch"]] = {
                "class_type": "ImageBatchMulti",
                "inputs": {
                    "inputcount": 2,
                    "Update inputs": None,
                    "image_1": [prev_image_ref, 0],
                    "image_2": [ids["vae_decode"], 0],
                },
                "_meta": {"title": f"Image Batch Multi seg{seg_idx}"},
            }

    # ═══════════════════════════════════════════════════════════════════════════
    # FINAL OUTPUT NODES
    # ═══════════════════════════════════════════════════════════════════════════

    # Get the final merged image (last ImageBatchMulti or first VAEDecode if only 1 segment)
    last_ids = seg_node_ids[-1]
    if len(segments) > 1:
        final_image_ref = [last_ids["batch"], 0]
    else:
        final_image_ref = [last_ids["vae_decode"], 0]

    # ColorMatch
    workflow["1546"] = {
        "class_type": "ColorMatch",
        "inputs": {
            "method": "mkl",
            "strength": 0.4,
            "multithread": True,
            "image_ref": ["97", 0],
            "image_target": final_image_ref,
        },
        "_meta": {"title": "Color Match"},
    }

    # Track current image source through the post-processing chain
    current_image_ref = ["1546", 0]

    # VRAMCleanup before post-processing: offload models to free VRAM for TRT engines
    if enable_upscale or enable_interpolation:
        workflow["pp_vram_cleanup"] = {
            "class_type": "VRAMCleanup",
            "inputs": {
                "offload_model": True,
                "offload_cache": True,
                "anything": current_image_ref,
            },
            "_meta": {"title": "VRAM Cleanup (pre-postproc)"},
        }
        current_image_ref = ["pp_vram_cleanup", 0]

    # ── Optional: TensorRT Upscale ───────────────────────────────────────
    if enable_upscale:
        workflow["pp_upscale_loader"] = {
            "class_type": "LoadUpscalerTensorrtModel",
            "inputs": {
                "model": upscale_model,
                "precision": "fp16",
            },
            "_meta": {"title": "Load Upscaler TRT"},
        }
        workflow["pp_upscale"] = {
            "class_type": "UpscalerTensorrt",
            "inputs": {
                "images": current_image_ref,
                "upscaler_trt_model": ["pp_upscale_loader", 0],
                "resize_to": upscale_resize,
                "resize_width": 1024,
                "resize_height": 1024,
            },
            "_meta": {"title": "Upscaler TensorRT"},
        }
        current_image_ref = ["pp_upscale", 0]

    # ── Optional: RIFE TensorRT Frame Interpolation ──────────────────────
    output_fps = fps
    if enable_interpolation and interpolation_multiplier > 1:
        output_fps = fps * interpolation_multiplier
        # Auto-select RIFE profile: use "large" if upscaled (dimensions > 1080px)
        rife_profile = interpolation_profile
        if enable_upscale:
            rife_profile = "large"
        workflow["pp_rife_loader"] = {
            "class_type": "AutoLoadRifeTensorrtModel",
            "inputs": {
                "model": "rife49_ensemble_True_scale_1_sim",
                "precision": "fp16",
                "resolution_profile": rife_profile,
            },
            "_meta": {"title": "Load RIFE TRT"},
        }
        workflow["pp_rife"] = {
            "class_type": "AutoRifeTensorrt",
            "inputs": {
                "frames": current_image_ref,
                "rife_trt_model": ["pp_rife_loader", 0],
                "clear_cache_after_n_frames": 100,
                "multiplier": interpolation_multiplier,
                "keep_model_loaded": False,
            },
            "_meta": {"title": f"RIFE {output_fps}FPS"},
        }
        current_image_ref = ["pp_rife", 0]

    # ── Optional: MMAudio ────────────────────────────────────────────────
    audio_ref = None
    if enable_mmaudio:
        workflow["pp_mma_model"] = {
            "class_type": "MMAudioModelLoader",
            "inputs": {
                "mmaudio_model": "mmaudio_large_44k_nsfw_gold_8.5k_final_fp16.safetensors",
                "base_precision": "fp16",
            },
            "_meta": {"title": "MMAudio Model"},
        }
        workflow["pp_mma_features"] = {
            "class_type": "MMAudioFeatureUtilsLoader",
            "inputs": {
                "vae_model": "mmaudio_vae_44k_fp16.safetensors",
                "synchformer_model": "mmaudio_synchformer_fp16.safetensors",
                "clip_model": "apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors",
                "mode": "44k",
                "precision": "fp16",
            },
            "_meta": {"title": "MMAudio Features"},
        }
        # Calculate video duration for MMAudio
        total_frames = sum(seg.get("num_frames", 81) for seg in segments)
        audio_duration = total_frames / fps
        workflow["pp_mma_sampler"] = {
            "class_type": "MMAudioSampler",
            "inputs": {
                "mmaudio_model": ["pp_mma_model", 0],
                "feature_utils": ["pp_mma_features", 0],
                "duration": audio_duration,
                "steps": mmaudio_steps,
                "cfg": mmaudio_cfg,
                "seed": random.randint(0, 1125899906842624),
                "prompt": mmaudio_prompt,
                "negative_prompt": mmaudio_negative_prompt,
                "mask_away_clip": False,
                "force_offload": True,
                "images": current_image_ref,
                "source_fps": float(fps),
            },
            "_meta": {"title": "MMAudio Sampler"},
        }
        audio_ref = ["pp_mma_sampler", 0]

    # ── Final output: VHS_VideoCombine ───────────────────────────────────
    final_output_inputs = {
        "frame_rate": output_fps,
        "loop_count": 0,
        "filename_prefix": "wan22_story",
        "format": "video/h264-mp4",
        "pix_fmt": "yuv420p",
        "crf": 19,
        "save_metadata": True,
        "trim_to_audio": bool(audio_ref),
        "pingpong": False,
        "save_output": True,
        "images": current_image_ref,
    }
    if audio_ref:
        final_output_inputs["audio"] = audio_ref

    workflow["1609"] = {
        "class_type": "VHS_VideoCombine",
        "inputs": final_output_inputs,
        "_meta": {"title": f"Final Output {output_fps}FPS"},
    }

    # Inject LoRAs via Power Lora Loader
    if loras:
        workflow = _inject_story_loras(workflow, loras)

    logger.info(f"Built fully aligned story workflow with {len(workflow)} nodes for {len(segments)} segments")

    return workflow


