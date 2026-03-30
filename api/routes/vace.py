import logging
import json
import random
import uuid
from typing import Optional, Literal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from api.config import WORKFLOWS_DIR, COMFYUI_VACE_URL, COMFYUI_PATH, UPLOADS_DIR
from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()

VACE_INPUT_DIR = COMFYUI_PATH / "input"


class VaceGenerateRequest(BaseModel):
    mode: Literal["ref2v", "v2v", "inpainting", "flf2v"] = Field(..., description="VACE mode")
    prompt: str = Field(..., min_length=1, max_length=2000)
    negative_prompt: str = Field(
        default="色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，"
        "最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，"
        "画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，"
        "杂乱的背景，三条腿，背景人很多，倒着走"
    )
    width: int = Field(default=832, ge=64, le=1920)
    height: int = Field(default=480, ge=64, le=1920)
    num_frames: int = Field(default=81, ge=5, le=200)
    fps: int = Field(default=16)
    steps: int = Field(default=20, ge=1, le=50)
    cfg: float = Field(default=6.0)
    shift: float = Field(default=5.0)
    seed: int = Field(default=-1)
    strength: float = Field(default=1.0, ge=0.0, le=10.0, description="VACE control strength")
    lightning: bool = Field(default=False, description="Use Lightning LoRA for 4-step fast generation")
    # File references (filenames in ComfyUI input dir)
    ref_image: Optional[str] = Field(default=None, description="Reference image filename (for ref2v, v2v, inpainting)")
    input_video: Optional[str] = Field(default=None, description="Input video filename (for v2v, inpainting)")
    mask_video: Optional[str] = Field(default=None, description="Mask video filename (for inpainting)")
    start_image: Optional[str] = Field(default=None, description="Start frame filename (for flf2v)")
    end_image: Optional[str] = Field(default=None, description="End frame filename (for flf2v)")


VACE_WORKFLOW_MAP = {
    "ref2v": "vace_ref2v_a14b.json",
    "v2v": "v2v_vace_a14b.json",
    "inpainting": "vace_inpainting_a14b.json",
    "flf2v": "vace_flf2v_a14b.json",
}

LIGHTNING_LORAS = {
    "high": "Wan22_T2V_HIGH_Lightning_4steps.safetensors",
    "low": "Wan22_T2V_LOW_Lightning_4steps.safetensors",
}


def _load_vace_template(name: str) -> dict:
    path = WORKFLOWS_DIR / name
    with open(path) as f:
        data = json.load(f)
    # Remove _meta if present
    data.pop("_meta", None)
    return data


def _build_vace_workflow(req: VaceGenerateRequest) -> dict:
    """Build a VACE workflow from template and parameters."""
    template_name = VACE_WORKFLOW_MAP.get(req.mode)
    if not template_name:
        raise ValueError(f"Unknown VACE mode: {req.mode}")

    wf = _load_vace_template(template_name)

    seed = req.seed if req.seed >= 0 else random.randint(0, 2**32)

    # Align dimensions
    width = (req.width // 16) * 16
    height = (req.height // 16) * 16
    num_frames = req.num_frames
    if (num_frames - 1) % 4 != 0:
        num_frames = ((num_frames - 1) // 4 + 1) * 4 + 1

    # Set common parameters across all nodes
    for node_id, node in wf.items():
        ct = node.get("class_type", "")
        inputs = node.get("inputs", {})

        if ct == "WanVideoTextEncode":
            inputs["positive_prompt"] = req.prompt
            if req.negative_prompt:
                inputs["negative_prompt"] = req.negative_prompt

        elif ct == "WanVideoVACEEncode":
            inputs["width"] = width
            inputs["height"] = height
            inputs["num_frames"] = num_frames
            inputs["strength"] = req.strength

        elif ct == "WanVideoSampler":
            inputs["seed"] = seed
            if not req.lightning:
                inputs["steps"] = req.steps
                inputs["cfg"] = req.cfg
            inputs["shift"] = req.shift

        elif ct == "VHS_VideoCombine":
            inputs["frame_rate"] = req.fps

        elif ct == "WanVideoVACEStartToEndFrame":
            inputs["num_frames"] = num_frames

    # Mode-specific inputs
    if req.mode == "ref2v":
        if req.ref_image:
            for nid, node in wf.items():
                if node.get("class_type") == "LoadImage":
                    node["inputs"]["image"] = req.ref_image

    elif req.mode == "v2v":
        if req.input_video:
            for nid, node in wf.items():
                if node.get("class_type") == "VHS_LoadVideo":
                    node["inputs"]["video"] = req.input_video
                    node["inputs"]["frame_load_cap"] = num_frames
        if req.ref_image:
            for nid, node in wf.items():
                if node.get("class_type") == "LoadImage":
                    node["inputs"]["image"] = req.ref_image
        else:
            # Remove ref_images from VACE encode if no reference
            for nid, node in wf.items():
                if node.get("class_type") == "WanVideoVACEEncode":
                    node["inputs"].pop("ref_images", None)
            # Remove LoadImage node
            to_remove = [nid for nid, n in wf.items() if n.get("class_type") == "LoadImage"]
            for nid in to_remove:
                del wf[nid]

    elif req.mode == "inpainting":
        if req.input_video:
            # Set source video (node 11)
            if "11" in wf:
                wf["11"]["inputs"]["video"] = req.input_video
                wf["11"]["inputs"]["frame_load_cap"] = num_frames
        if req.mask_video:
            # Set mask video (node 20)
            if "20" in wf:
                wf["20"]["inputs"]["video"] = req.mask_video
                wf["20"]["inputs"]["frame_load_cap"] = num_frames
        if req.ref_image:
            if "12" in wf:
                wf["12"]["inputs"]["image"] = req.ref_image
        else:
            # Remove ref_images from VACE encode
            for nid, node in wf.items():
                if node.get("class_type") == "WanVideoVACEEncode":
                    node["inputs"].pop("ref_images", None)
            if "12" in wf:
                del wf["12"]

    elif req.mode == "flf2v":
        if req.start_image:
            if "18" in wf:
                wf["18"]["inputs"]["image"] = req.start_image
        if req.end_image:
            if "19" in wf:
                wf["19"]["inputs"]["image"] = req.end_image
        else:
            # Remove end_image from StartToEndFrame node
            for nid, node in wf.items():
                if node.get("class_type") == "WanVideoVACEStartToEndFrame":
                    node["inputs"].pop("end_image", None)
            if "19" in wf:
                del wf["19"]

    # Lightning mode: inject LoRA + adjust steps/cfg
    if req.lightning:
        # Find model nodes (after BlockSwap)
        high_model_node = low_model_node = None
        for nid, node in wf.items():
            if node.get("class_type") == "WanVideoSetBlockSwap":
                ref = node["inputs"].get("model", [])
                if isinstance(ref, list):
                    src_nid = ref[0]
                    src = wf.get(src_nid, {})
                    model_name = src.get("inputs", {}).get("model", "")
                    if "HIGH" in model_name.upper():
                        high_model_node = nid
                    elif "LOW" in model_name.upper():
                        low_model_node = nid

        next_id = max(int(k) for k in wf.keys() if k.isdigit()) + 1

        # HIGH Lightning LoRA
        if high_model_node:
            lora_select_id = str(next_id)
            lora_set_id = str(next_id + 1)
            wf[lora_select_id] = {
                "class_type": "WanVideoLoraSelect",
                "inputs": {
                    "lora": LIGHTNING_LORAS["high"],
                    "strength": 1.0,
                    "merge_loras": False,
                },
            }
            wf[lora_set_id] = {
                "class_type": "WanVideoSetLoRAs",
                "inputs": {
                    "model": [high_model_node, 0],
                    "lora": [lora_select_id, 0],
                },
            }
            # Rewire HIGH sampler to use LoRA model
            for nid, node in wf.items():
                if node.get("class_type") == "WanVideoSampler":
                    inp = node["inputs"]
                    if isinstance(inp.get("model"), list) and inp["model"][0] == high_model_node:
                        inp["model"] = [lora_set_id, 0]
            next_id += 2

        # LOW Lightning LoRA
        if low_model_node:
            lora_select_id = str(next_id)
            lora_set_id = str(next_id + 1)
            wf[lora_select_id] = {
                "class_type": "WanVideoLoraSelect",
                "inputs": {
                    "lora": LIGHTNING_LORAS["low"],
                    "strength": 1.0,
                    "merge_loras": False,
                },
            }
            wf[lora_set_id] = {
                "class_type": "WanVideoSetLoRAs",
                "inputs": {
                    "model": [low_model_node, 0],
                    "lora": [lora_select_id, 0],
                },
            }
            for nid, node in wf.items():
                if node.get("class_type") == "WanVideoSampler":
                    inp = node["inputs"]
                    if isinstance(inp.get("model"), list) and inp["model"][0] == low_model_node:
                        inp["model"] = [lora_set_id, 0]
            next_id += 2

        # Set Lightning sampling params
        for nid, node in wf.items():
            if node.get("class_type") == "WanVideoSampler":
                inp = node["inputs"]
                inp["steps"] = 4
                inp["cfg"] = 1.0
                # Adjust step split for 4 total steps
                if inp.get("start_step", 0) == 0 and inp.get("end_step", -1) > 0:
                    inp["end_step"] = 2
                elif inp.get("start_step", 0) > 0:
                    inp["start_step"] = 2
    else:
        # Standard mode: dynamic step split
        split_step = req.steps // 2
        for nid, node in wf.items():
            if node.get("class_type") == "WanVideoSampler":
                inp = node["inputs"]
                if inp.get("start_step", 0) == 0 and inp.get("end_step", -1) > 0:
                    inp["end_step"] = split_step
                elif inp.get("start_step", 0) > 0:
                    inp["start_step"] = split_step

    return wf


@router.post("/vace/upload")
async def upload_vace_file(
    file: UploadFile = File(...),
    _: str = Depends(verify_api_key),
):
    """Upload a file (image/video/mask) to ComfyUI input directory for VACE workflows."""
    ext = Path(file.filename).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".avif", ".mp4", ".mov", ".avi", ".gif"}:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    filename = f"vace_{uuid.uuid4().hex[:12]}{ext}"
    dest = VACE_INPUT_DIR / filename

    content = await file.read()
    dest.write_bytes(content)
    logger.info("Uploaded VACE file: %s (%d bytes)", filename, len(content))

    return {"filename": filename, "size": len(content)}


@router.post("/vace/generate")
async def generate_vace(
    req: VaceGenerateRequest,
    _: str = Depends(verify_api_key),
):
    """Submit a VACE workflow to ComfyUI and return prompt_id for tracking."""
    import aiohttp

    try:
        workflow = _build_vace_workflow(req)
    except Exception as e:
        raise HTTPException(400, f"Failed to build workflow: {e}")

    payload = {"prompt": workflow}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{COMFYUI_VACE_URL}/prompt",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise HTTPException(502, f"ComfyUI error: {body[:500]}")
                result = await resp.json()
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"Cannot connect to VACE ComfyUI: {e}")

    prompt_id = result.get("prompt_id")
    logger.info("VACE %s submitted: prompt_id=%s, lightning=%s", req.mode, prompt_id, req.lightning)

    return {
        "prompt_id": prompt_id,
        "mode": req.mode,
        "lightning": req.lightning,
        "comfyui_url": COMFYUI_VACE_URL,
    }


@router.get("/vace/status/{prompt_id}")
async def get_vace_status(
    prompt_id: str,
    _: str = Depends(verify_api_key),
):
    """Check VACE task status from ComfyUI history."""
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            # Check queue
            async with session.get(
                f"{COMFYUI_VACE_URL}/queue",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                queue = await resp.json()

            running = any(
                item[1] == prompt_id
                for item in queue.get("queue_running", [])
            )
            pending = any(
                item[1] == prompt_id
                for item in queue.get("queue_pending", [])
            )

            # Check history
            async with session.get(
                f"{COMFYUI_VACE_URL}/history/{prompt_id}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                history = await resp.json()
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"Cannot connect to VACE ComfyUI: {e}")

    if prompt_id not in history:
        if running:
            return {"status": "running", "progress": 0}
        if pending:
            return {"status": "queued"}
        return {"status": "unknown"}

    h = history[prompt_id]
    status = h.get("status", {})
    status_str = status.get("status_str", "unknown")

    if status_str == "error":
        error_msg = ""
        for msg in status.get("messages", []):
            if isinstance(msg, list) and msg[0] == "execution_error":
                error_msg = msg[1].get("exception_message", "")[:500]
        return {"status": "failed", "error": error_msg}

    # Extract output video
    outputs = h.get("outputs", {})
    videos = []
    for nid, out in outputs.items():
        for g in out.get("gifs", []):
            videos.append({
                "filename": g.get("filename"),
                "subfolder": g.get("subfolder", ""),
                "type": g.get("type", "output"),
            })

    # Calculate duration from messages
    msgs = status.get("messages", [])
    start_ts = end_ts = None
    for msg in msgs:
        if isinstance(msg, list) and len(msg) > 1 and isinstance(msg[1], dict):
            if msg[0] == "execution_start":
                start_ts = msg[1].get("timestamp", 0) / 1000
            if msg[0] == "execution_success":
                end_ts = msg[1].get("timestamp", 0) / 1000
    duration = (end_ts - start_ts) if start_ts and end_ts else None

    return {
        "status": "completed" if videos else "running",
        "videos": videos,
        "duration_seconds": round(duration, 1) if duration else None,
    }


@router.get("/vace/video/{filename}")
async def get_vace_video(
    filename: str,
    subfolder: str = "",
):
    """Serve a VACE output video file."""
    from fastapi.responses import FileResponse

    output_dir = COMFYUI_PATH / "output"
    if subfolder:
        file_path = output_dir / subfolder / filename
    else:
        file_path = output_dir / filename

    if not file_path.exists():
        raise HTTPException(404, "Video not found")

    return FileResponse(file_path, media_type="video/mp4", filename=filename)
