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


def _load_lora_trigger_words() -> dict[str, list[str]]:
    """Load name -> trigger_words mapping from loras.yaml."""
    try:
        with open(LORAS_PATH) as f:
            data = yaml.safe_load(f)
        return {
            item["name"]: item.get("trigger_words", [])
            for item in data.get("loras", [])
            if "name" in item
        }
    except Exception as e:
        logger.warning(f"Failed to load trigger words: {e}")
        return {}


def _inject_trigger_words(prompt: str, loras: list[LoraInput]) -> str:
    """Collect trigger words from selected LoRAs and prepend to prompt."""
    tw_map = _load_lora_trigger_words()
    all_words = []
    prompt_lower = prompt.lower()
    for lora in loras:
        for word in tw_map.get(lora.name, []):
            if word.lower() not in prompt_lower and word not in all_words:
                all_words.append(word)
    if not all_words:
        return prompt
    return ", ".join(all_words) + ", " + prompt


_lora_name_map: dict[str, str] = _load_lora_name_map()
DIFFUSION_DIR = COMFYUI_PATH / "models" / "diffusion_models"

# Model presets: name -> {high: filename, low: filename, quantization: str}
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
    },
}


def get_model_presets() -> list[dict]:
    """Return available model presets for the API."""
    result = []
    for name, info in MODEL_PRESETS.items():
        # Check if files exist
        h_exists = (DIFFUSION_DIR / info["high"]).exists()
        l_exists = (DIFFUSION_DIR / info["low"]).exists()
        result.append({
            "name": name,
            "high": info["high"],
            "low": info["low"],
            "quantization": info["quantization"],
            "available": h_exists and l_exists,
        })
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
    max_id = max(int(k) for k in workflow.keys())

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

    max_id = max(int(k) for k in workflow.keys())

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
) -> dict:
    # Use enhanced template when I2V features are enabled
    if mode == GenerateMode.I2V and model == ModelType.A14B and (motion_amplitude > 0 or color_match):
        template_name = "i2v_a14b_enhanced.json"
    else:
        template_name = WORKFLOW_MAP[(mode, model)]
    workflow = _load_template(template_name)

    if seed is None:
        seed = random.randint(0, 2**63)

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
