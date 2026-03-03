import logging
import json
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Body, UploadFile, File, Form
from api.models.schemas import GenerateResponse
from api.models.enums import GenerateMode, ModelType, TaskStatus
from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/workflow/run", response_model=GenerateResponse)
async def run_custom_workflow(
    workflow_name: str = Body(...),
    params: dict = Body(...),
    model: str = Body("a14b"),
    _=Depends(verify_api_key)
):
    """
    Run a custom ComfyUI workflow with parameter substitution.

    Args:
        workflow_name: Name of workflow file (without .json extension)
        params: Dictionary of parameters to substitute in workflow
        model: Model type (a14b or 5b)

    Example params:
    {
        "prompt": "A woman walking",
        "width": 832,
        "height": 480,
        "duration": 3.3,  // Will be converted to num_frames (81 @ 24fps)
        "fps": 24,
        "steps": 20,
        "cfg": 6.0,
        "shift": 5.0,
        "seed": -1
    }

    Note: You can use either "duration" (in seconds) or "num_frames" directly.
    If "duration" is provided, it will be converted to num_frames using 4n+1 format.
    """
    from api.main import task_manager
    from api.config import PROJECT_ROOT

    # Convert duration to num_frames if provided
    if "duration" in params and "num_frames" not in params:
        duration = float(params["duration"])
        fps = int(params.get("fps", 24))
        # Convert to 4n+1 format
        frames = max(round(duration * fps), 1)
        frames = round((frames - 1) / 4) * 4 + 1
        params["num_frames"] = max(frames, 5)
        logger.info(f"Converted duration {duration}s @ {fps}fps to {params['num_frames']} frames")

    # Load workflow file
    workflow_path = PROJECT_ROOT / "workflows" / f"{workflow_name}.json"
    if not workflow_path.exists():
        raise HTTPException(404, f"Workflow '{workflow_name}' not found")

    try:
        with open(workflow_path) as f:
            workflow = json.load(f)
    except Exception as e:
        raise HTTPException(400, f"Failed to load workflow: {e}")

    # Detect ComfyUI UI export format (has "nodes" list instead of node-id keys)
    # ComfyUI /prompt API requires API format: {"1": {"class_type": ..., "inputs": ...}, ...}
    # UI format files (exported from ComfyUI browser) cannot be used directly.
    if "nodes" in workflow and isinstance(workflow.get("nodes"), list):
        raise HTTPException(
            400,
            f"Workflow '{workflow_name}' is in ComfyUI UI export format. "
            "Please use the API format workflow (export via 'Save (API format)' in ComfyUI). "
            "UI format files contain 'nodes'/'links' lists and cannot be submitted to /prompt directly."
        )

    # Validate model
    try:
        model_type = ModelType(model)
    except ValueError:
        raise HTTPException(400, f"Invalid model: {model}")

    # Check if ComfyUI instance is alive
    client = task_manager.clients.get(model_type.value)
    if not client or not await client.is_alive():
        raise HTTPException(503, f"ComfyUI {model_type.value} instance is not available")

    # Substitute parameters in workflow
    # Supports both UI format (nodes list) and API format (node-id dict)

    is_api_format = all(
        isinstance(v, dict) and "class_type" in v
        for v in workflow.values()
        if isinstance(v, dict)
    )

    # 1. Simple placeholder replacement: ${param_name}
    workflow_str = json.dumps(workflow)
    for key, value in params.items():
        placeholder = f"${{{key}}}"
        workflow_str = workflow_str.replace(placeholder, str(value))
    workflow = json.loads(workflow_str)

    if is_api_format:
        # --- API format: {"node_id": {"class_type": ..., "inputs": {...}, "_meta": {"title": ...}}} ---
        # Map params to (class_type, input_key) pairs for direct injection
        api_param_rules = {
            "num_frames": [
                ("PainterI2V", "length"),
                ("PainterLongVideo", "length"),
            ],
            "steps": [
                ("WanMoeKSamplerAdvanced", "steps"),
                ("KSampler", "steps"),
                ("KSamplerAdvanced", "steps"),
            ],
            "cfg": [
                ("WanMoeKSamplerAdvanced", "cfg"),
                ("KSampler", "cfg"),
            ],
            "shift": [
                ("PrimitiveFloat", "value"),  # "Sigma Shift" node
            ],
            "seed": [
                ("Seed (rgthree)", "seed"),
            ],
            "sampler_name": [
                ("WanMoeKSamplerAdvanced", "sampler_name"),
            ],
            "scheduler": [
                ("WanMoeKSamplerAdvanced", "scheduler"),
            ],
            "fps": [
                ("VHS_VideoCombine", "frame_rate"),
            ],
            "motion_amplitude": [
                ("FloatConstant", "value"),  # "motion amplitude" node (only one in workflow)
            ],
            "motion_frames": [
                ("INTConstant", "value"),  # "motion_frames" node (only one in workflow)
            ],
        }

        for param_key, param_value in params.items():
            rules = api_param_rules.get(param_key, [])
            for class_type, input_key in rules:
                for nid, node in workflow.items():
                    if node.get("class_type") == class_type and input_key in node.get("inputs", {}):
                        old = node["inputs"][input_key]
                        # Don't overwrite link references (lists)
                        if isinstance(old, list):
                            continue
                        node["inputs"][input_key] = param_value
                        logger.debug("API param %s=%s -> node %s (%s) input %s", param_key, param_value, nid, class_type, input_key)

        # Handle prompt injection: find CLIPTextEncode nodes titled "Positive encode"
        prompt_val = params.get("prompt")
        if prompt_val:
            for nid, node in workflow.items():
                title = node.get("_meta", {}).get("title", "")
                if node.get("class_type") == "CLIPTextEncode" and "positive" in title.lower():
                    node["inputs"]["text"] = prompt_val

        # Handle per-segment prompts: prompt_1, prompt_2, prompt_3, prompt_4
        # Override the "Prompt N" display nodes (easy showAnything) by replacing
        # their "anything" input with the user's text, disconnecting StorySplitNode
        for i in range(1, 5):
            seg_prompt = params.get(f"prompt_{i}")
            if seg_prompt:
                target_title = f"Prompt {i}"
                for nid, node in workflow.items():
                    title = node.get("_meta", {}).get("title", "")
                    if title == target_title and node.get("class_type") == "easy showAnything":
                        node["inputs"]["anything"] = seg_prompt
                        logger.debug("Injected prompt_%d into node %s", i, nid)

        # Handle per-segment frame counts: num_frames_1, num_frames_2, num_frames_3, num_frames_4
        # Also support duration_1..4 (converted to frames)
        # Segment order: PainterI2V first, then PainterLongVideo in title order
        painter_nodes = []
        for nid, node in workflow.items():
            ct = node.get("class_type", "")
            if ct == "PainterI2V" and not isinstance(node.get("inputs", {}).get("length"), list):
                painter_nodes.append((0, nid, node))  # segment 1 is always PainterI2V
            elif ct == "PainterLongVideo" and not isinstance(node.get("inputs", {}).get("length"), list):
                painter_nodes.append((1, nid, node))  # segments 2+ are PainterLongVideo
        # Sort: PainterI2V first, then PainterLongVideo by node id
        painter_nodes.sort(key=lambda x: (x[0], x[1]))

        for seg_idx, (_, nid, node) in enumerate(painter_nodes):
            seg_num = seg_idx + 1
            # Check for duration_N first, then num_frames_N
            seg_duration = params.get(f"duration_{seg_num}")
            seg_frames = params.get(f"num_frames_{seg_num}")
            if seg_duration is not None and seg_frames is None:
                duration = float(seg_duration)
                fps = int(params.get("fps", 24))
                frames = max(round(duration * fps), 1)
                frames = round((frames - 1) / 4) * 4 + 1
                seg_frames = max(frames, 5)
                logger.info(f"Segment {seg_num}: duration {duration}s -> {seg_frames} frames")
            if seg_frames is not None:
                node["inputs"]["length"] = int(seg_frames)
                logger.debug("Per-segment frames: segment %d node %s -> %s frames", seg_num, nid, seg_frames)

        # Handle input image: inject uploaded filename into LoadImage nodes
        input_image = params.get("_input_image")
        if input_image:
            for nid, node in workflow.items():
                if node.get("class_type") == "LoadImage" and "image" in node.get("inputs", {}):
                    node["inputs"]["image"] = input_image
                    logger.info("Injected input image '%s' into LoadImage node %s", input_image, nid)

    else:
        # --- UI format: {"nodes": [...], "links": [...]} ---
        node_title_map = {
            "num_frames": "Lenght",
            "length": "Lenght",
            "lenght": "Lenght",
            "steps": "Steps",
            "width": "WIDTH",
            "height": "HEIGHT",
            "prompt": "prompt_1",
            "prompt_1": "prompt_1",
            "prompt_2": "prompt_2",
            "prompt_3": "prompt_3",
            "prompt_4": "prompt_4",
        }

        for param_key, param_value in params.items():
            node_title = node_title_map.get(param_key.lower(), param_key)

            for node in workflow.get("nodes", []):
                title = node.get("title", "")

                if node.get("type") == "mxSlider" and title == node_title:
                    if "widgets_values" in node and len(node["widgets_values"]) >= 2:
                        node["widgets_values"][0] = int(param_value) if isinstance(param_value, (int, float)) else param_value
                        node["widgets_values"][1] = int(param_value) if isinstance(param_value, (int, float)) else param_value

                elif "prompt" in title.lower() and "prompt" in param_key.lower():
                    if "widgets_values" in node:
                        node["widgets_values"][0] = str(param_value)

    # Create task
    task_id = await task_manager.create_task(
        GenerateMode.T2V,  # Use T2V as default mode
        model_type,
        workflow,
        params=params
    )

    return GenerateResponse(task_id=task_id, status=TaskStatus.QUEUED)


@router.post("/workflow/run-with-image", response_model=GenerateResponse)
async def run_workflow_with_image(
    workflow_name: str = Form(...),
    params: str = Form(...),
    model: str = Form("a14b"),
    image: Optional[UploadFile] = File(None),
    _=Depends(verify_api_key)
):
    """Run workflow with optional image upload (FormData)."""
    from api.main import task_manager
    from api.config import PROJECT_ROOT

    try:
        params_dict = json.loads(params)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid params JSON: {e}")

    # If image provided, upload to ComfyUI and inject into LoadImage nodes
    uploaded_filename = None
    if image and image.filename:
        image_data = await image.read()
        if len(image_data) > 0:
            model_type_val = model
            try:
                mt = ModelType(model_type_val)
            except ValueError:
                raise HTTPException(400, f"Invalid model: {model_type_val}")

            client = task_manager.clients.get(mt.value)
            if not client or not await client.is_alive():
                raise HTTPException(503, f"ComfyUI {mt.value} instance is not available")

            # Upload image to ComfyUI input directory
            upload_result = await client.upload_image(image_data, image.filename)
            uploaded_filename = upload_result.get("name", image.filename)
            logger.info("Uploaded image to ComfyUI: %s", uploaded_filename)

    # Inject uploaded_filename into params so the main logic can use it
    if uploaded_filename:
        params_dict["_input_image"] = uploaded_filename

    # Delegate to the main workflow run logic
    return await run_custom_workflow(
        workflow_name=workflow_name,
        params=params_dict,
        model=model,
    )


@router.get("/workflow/list")
async def list_workflows(_=Depends(verify_api_key)):
    """List all available workflow files."""
    from api.config import PROJECT_ROOT

    workflows_dir = PROJECT_ROOT / "workflows"
    if not workflows_dir.exists():
        return {"workflows": []}

    workflows = []
    for file in workflows_dir.glob("*.json"):
        workflows.append({
            "name": file.stem,
            "filename": file.name,
            "size": file.stat().st_size
        })

    return {"workflows": workflows}
