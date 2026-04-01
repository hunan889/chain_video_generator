"""Advanced workflow routes — generate, status, cancel, regenerate."""

import json
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api_gateway.dependencies import get_gateway
from api_gateway.services.workflow_engine import (
    STAGE_NAMES, STAGE_WEIGHTS, build_default_internal_config,
)
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["workflow-advanced"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class WorkflowGenerateRequest(BaseModel):
    mode: Literal["t2v", "first_frame", "face_reference", "full_body_reference"] = "t2v"
    user_prompt: str = Field(default="", max_length=2000)
    pose_keys: Optional[list[str]] = None
    reference_image: Optional[str] = None
    resolution: Optional[str] = None
    aspect_ratio: Optional[str] = None
    duration: Optional[int] = None
    first_frame_source: Optional[str] = None
    uploaded_first_frame: Optional[str] = None
    auto_analyze: bool = True
    auto_lora: bool = True
    auto_prompt: bool = True
    t2i_params: Optional[dict] = None
    seedream_params: Optional[dict] = None
    video_params: Optional[dict] = None
    internal_config: Optional[dict] = None
    turbo: Optional[bool] = True
    mmaudio: Optional[dict] = None
    parent_workflow_id: Optional[str] = None


class WorkflowStage(BaseModel):
    name: str
    status: str
    error: Optional[str] = None


class WorkflowGenerateResponse(BaseModel):
    workflow_id: str
    status: str
    current_stage: str = "prompt_analysis"
    stages: list[WorkflowStage] = []
    chain_id: Optional[str] = None
    final_video_url: Optional[str] = None
    first_frame_url: Optional[str] = None
    edited_frame_url: Optional[str] = None
    error: Optional[str] = None
    progress: Optional[float] = None
    elapsed_time: Optional[float] = None
    parent_workflow_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_engine(request: Request):
    engine = getattr(request.app.state, "workflow_engine", None)
    if engine is None:
        raise HTTPException(503, "Workflow engine not initialized")
    return engine


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/workflow/generate-advanced", response_model=WorkflowGenerateResponse)
async def generate_advanced_workflow(req: WorkflowGenerateRequest, request: Request):
    """Create and start an advanced workflow."""
    engine = _get_engine(request)
    try:
        result = await engine.start_workflow(req.model_dump())
        return WorkflowGenerateResponse(**result)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Failed to start workflow")
        raise HTTPException(500, f"Failed to start workflow: {e}")


@router.get("/workflow/status/{workflow_id}")
async def get_workflow_status(
    workflow_id: str,
    detail: bool = Query(False),
    request: Request = None,
    gw: TaskGateway = Depends(get_gateway),
):
    """Get workflow status with stage progress."""
    redis = gw.redis
    wf_key = f"workflow:{workflow_id}"
    data = await redis.hgetall(wf_key)

    if not data:
        raise HTTPException(404, f"Workflow {workflow_id} not found")

    # Build stages
    stages = []
    stage_details = {}
    for name in STAGE_NAMES:
        s_status = data.get(f"stage_{name}", "pending")
        s_error = data.get(f"stage_{name}_error")
        stages.append({"name": name, "status": s_status, "error": s_error})
        # Parse stage details
        sd_raw = data.get(f"stage_{name}_details")
        if sd_raw:
            try:
                stage_details[name] = json.loads(sd_raw)
            except (json.JSONDecodeError, TypeError):
                pass

    # Calculate progress
    progress = 0.0
    for s in stages:
        weight = STAGE_WEIGHTS.get(s["name"], 0)
        if s["status"] == "completed":
            progress += weight
        elif s["status"] == "running":
            # For video_generation, read task progress directly from Redis
            if s["name"] == "video_generation":
                chain_id = data.get("chain_id")
                if chain_id:
                    # Read chain → find current task → read task progress directly
                    import json as _json
                    chain_raw = await redis.hgetall(f"chain:{chain_id}")
                    if chain_raw:
                        try:
                            task_ids = _json.loads(chain_raw.get("segment_task_ids", "[]"))
                            if task_ids:
                                task_raw = await redis.hgetall(f"task:{task_ids[0]}")
                                if task_raw:
                                    tp = float(task_raw.get("progress", 0))
                                    progress += weight * tp
                        except Exception:
                            pass
            else:
                progress += weight * 0.5  # assume 50% for non-video stages

    # Elapsed time
    elapsed = None
    try:
        created = float(data.get("created_at", 0))
        completed = float(data.get("completed_at", 0))
        if created:
            elapsed = (completed if completed else __import__("time").time()) - created
    except (ValueError, TypeError):
        pass

    result = {
        "workflow_id": workflow_id,
        "status": data.get("status", "unknown"),
        "current_stage": data.get("current_stage", ""),
        "stages": stages,
        "chain_id": data.get("chain_id"),
        "final_video_url": data.get("final_video_url"),
        "first_frame_url": data.get("first_frame_url"),
        "edited_frame_url": data.get("edited_frame_url"),
        "error": data.get("error"),
        "progress": round(progress, 3),
        "elapsed_time": round(elapsed, 1) if elapsed else None,
        "parent_workflow_id": data.get("parent_workflow_id"),
    }

    if detail:
        result["user_prompt"] = data.get("user_prompt")
        result["mode"] = data.get("mode")
        result["reference_image"] = None  # Don't return large base64
        result["stage_details"] = stage_details
        try:
            result["created_at"] = int(float(data.get("created_at", 0)))
        except (ValueError, TypeError):
            result["created_at"] = None
        try:
            result["completed_at"] = int(float(data.get("completed_at", 0))) if data.get("completed_at") else None
        except (ValueError, TypeError):
            result["completed_at"] = None
        ar_raw = data.get("analysis_result")
        if ar_raw:
            try:
                result["analysis_result"] = json.loads(ar_raw)
            except (json.JSONDecodeError, TypeError):
                pass

    return result


@router.post("/workflow/{workflow_id}/cancel")
async def cancel_workflow(workflow_id: str, request: Request):
    """Cancel a running workflow."""
    engine = _get_engine(request)
    success = await engine.cancel_workflow(workflow_id)
    if not success:
        raise HTTPException(409, f"Cannot cancel workflow {workflow_id}")
    return {"cancelled": True, "workflow_id": workflow_id}


@router.post("/workflow/{workflow_id}/regenerate")
async def regenerate_workflow(workflow_id: str, request: Request):
    """Regenerate a workflow with same parameters."""
    engine = _get_engine(request)
    try:
        result = await engine.regenerate(workflow_id)
        return result
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/workflow/default-config")
async def get_default_config(
    mode: str = Query(...),
    turbo: bool = Query(False),
    resolution: str = Query("1080p"),
):
    """Return computed default internal_config for mode/turbo/resolution."""
    if mode not in ("t2v", "first_frame", "face_reference", "full_body_reference"):
        raise HTTPException(400, f"Invalid mode: {mode}")
    return build_default_internal_config(mode, turbo, resolution)


@router.get("/workflow/list")
async def list_workflows(request: Request):
    """List available workflow JSON template files."""
    import os
    workflows_dir = request.app.state.config.workflows_dir
    if not workflows_dir or not os.path.isdir(workflows_dir):
        return {"workflows": []}
    results = []
    for f in sorted(os.listdir(workflows_dir)):
        if f.endswith(".json"):
            path = os.path.join(workflows_dir, f)
            results.append({
                "name": f.replace(".json", ""),
                "filename": f,
                "size": os.path.getsize(path),
            })
    return {"workflows": results}
